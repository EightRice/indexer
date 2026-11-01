"""Mirror, debounced flushes, startup diff and GC (CONTRACT.md section 5)."""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import layout as L
from .build import Doc, build_list, build_net_head, build_dao_head
from .encoding import canonical, encode_value, obj_hash, sort_value
from .notify import make_msg, resync_obj, MSG_MAX, QUEUE_MAX_BYTES
from .targets import with_retry

log = logging.getLogger("publisher")

ENTRY_STALE = 200 * 1024
DEBOUNCE_QUIET = 1.0
DEBOUNCE_MAX = 3.0
GC_EVERY = 3600.0
GC_GRACE = 7200.0
NET = "net"
ALL = "*"


class Resync(Exception):
    pass


class RWLock:
    def __init__(self):
        self._c = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting = 0   # writers waiting: new readers queue behind them, so GC cannot starve

    def shared(self):
        rw = self

        class _S:
            def __enter__(s):
                with rw._c:
                    while rw._writer or rw._waiting:
                        rw._c.wait()
                    rw._readers += 1

            def __exit__(s, *a):
                with rw._c:
                    rw._readers -= 1
                    rw._c.notify_all()
        return _S()

    def exclusive(self):
        rw = self

        class _X:
            def __enter__(s):
                with rw._c:
                    rw._waiting += 1
                    while rw._writer or rw._readers:
                        rw._c.wait()
                    rw._waiting -= 1
                    rw._writer = True

            def __exit__(s, *a):
                with rw._c:
                    rw._writer = False
                    rw._c.notify_all()
        return _X()


@dataclass
class Scope:
    key: str
    channel: str
    ptr_path: str
    lists: dict = field(default_factory=dict)     # list key -> ListBuild
    dirty: set = field(default_factory=lambda: {ALL})
    head_hash: object = None                      # head in the written pointer
    counts: object = None
    seq: int = 0
    reach: set = field(default_factory=set)
    pending: dict = field(default_factory=dict)   # path -> True (coalesced)
    first_at: object = None
    last_at: object = None
    retry_at: float = 0.0
    backoff: float = 1.0
    flushing: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


def is_checksummed(addr):
    try:
        from eth_utils import to_checksum_address
        return to_checksum_address(addr) == addr
    except Exception:
        return False


class Publisher:
    def __init__(self, N, C, target, notifier=None, networks_index=False,
                 clock=time.time, mono=time.monotonic, uploads=16, flushes=8):
        if not L.NET_RE.match(N):
            raise ValueError(f"network name {N!r} does not match [A-Za-z0-9-]{{1,64}}")
        self.N, self.C, self.target, self.notifier = N, C, target, notifier
        self.networks_index = networks_index
        self.clock, self.mono = clock, mono
        self.lock = threading.RLock()
        self.rw = RWLock()
        self.mirror = {}        # path -> Doc
        self.place = {}         # path -> Place
        self.idx = {}           # dao -> list key -> lowercase path -> set(paths)
        self.tomb = {}          # path -> v of the delete
        self.first_read = {}    # group name -> read_time µs of that listener's first snapshot
        self.scopes = {}
        self.known = set()      # object hashes present in the target
        self.ref = {}           # hash -> number of scopes reaching it
        self.unreach = {}       # hash -> wall time it became unreachable
        self.contracts = {}     # id -> encoded data (checkpoint fields dropped)
        self.networks_raw = None
        self.networks_pending = None
        self.warned = set()
        self.fatal = None
        self.started = False
        self.last_gc = None
        self.puts = 0
        self.up_pool = ThreadPoolExecutor(uploads, thread_name_prefix="put")
        self.flush_pool = ThreadPoolExecutor(flushes, thread_name_prefix="flush")
        self._stop = False
        self.health = None      # callable returning an error string when a listener has died
        if notifier is not None:
            notifier.on_stale = self.on_stale

    # ---------- input ----------

    def _warn_once(self, key, msg, *args):
        if key not in self.warned:
            self.warned.add(key)
            log.warning(msg, *args)

    def _scope(self, key):
        st = self.scopes.get(key)
        if st is None:
            if key == NET:
                st = Scope(NET, L.net_channel(self.N), L.net_ptr_path(self.N))
            else:
                st = Scope(key, L.dao_channel(self.N, key), L.dao_ptr_path(self.N, key))
            self.scopes[key] = st
        return st

    def _mark(self, key, path, list_key):
        st = self._scope(key)
        now = self.mono()
        st.pending[path] = True
        st.first_at = st.first_at if st.first_at is not None else now
        st.last_at = now
        st.dirty.add(list_key)

    def apply(self, path, v, data, read_time):
        """One snapshot change: data None = deleted (v ignored, read_time is the delete's v)."""
        place, why = L.classify(path, self.C)
        if place is None:
            if why != "other collection":
                self._warn_once(("ignore", why), "ignoring documents under %s/.../%s", self.C, why)
            return
        try:
            with self.lock:
                old = self.mirror.get(path)
                if data is None:
                    if old is None:
                        return
                    del self.mirror[path]
                    self._unindex(place, path)
                    self.tomb[path] = read_time
                else:
                    if old is not None and old.v == v:
                        return
                    doc_id = path.rsplit("/", 1)[1]
                    entry = {"path": path, "id": doc_id, "v": v, "data": encode_value(data)}
                    k = 0 if place.spec.key == L.ID_ORDER else sort_value(entry["data"].get(place.spec.key))
                    self.mirror[path] = Doc(path, doc_id, v, k, entry, canonical(entry))
                    self.place[path] = place
                    self.idx.setdefault(place.dao, {}).setdefault(place.list_key, {}) \
                        .setdefault(path.lower(), set()).add(path)
                    self.tomb.pop(path, None)
                self._mark(place.dao, path, place.list_key)
                if place.list_key == L.DOC:
                    self._mark(NET, path, "daos")
        except Exception as e:
            self.fatal = e
            log.critical("cannot mirror %s: %s", path, e)
            raise

    def _unindex(self, place, path):
        s = self.idx.get(place.dao, {})
        lk = s.get(place.list_key, {})
        paths = lk.get(path.lower())
        if paths is not None:
            paths.discard(path)
            if not paths:
                del lk[path.lower()]
        if not lk:
            s.pop(place.list_key, None)
        if not s:
            self.idx.pop(place.dao, None)

    def apply_contract(self, doc_id, data):
        with self.lock:
            if data is None:
                self.contracts.pop(doc_id, None)
            else:
                kept = {k: v for k, v in data.items()
                        if not (k.lower() == "lastsyncedblock" or k.lower().endswith("lastsyncedblock"))}
                self.contracts[doc_id] = encode_value(kept)
            if self.networks_pending is None:
                self.networks_pending = self.mono()

    # ---------- building ----------

    def _pick(self, paths):
        if len(paths) == 1:
            return next(iter(paths))
        ordered = sorted(paths)
        cs = [p for p in ordered if is_checksummed(p.split("/")[1])]
        chosen = cs[0] if cs else ordered[0]
        self._warn_once(("dup", chosen.lower()), "several docs map to %s; publishing %s", chosen.lower(), chosen)
        return chosen

    def _docs(self, groups):
        return [self.mirror[self._pick(paths)] for paths in groups.values()]

    def _present(self, key):
        if key == NET:
            return {lp for s in self.idx.values() for lp in s.get(L.DOC, {})}
        return {lp for groups in self.idx.get(key, {}).values() for lp in groups}

    def _build(self, st):
        """Rebuild dirty lists and the head. Returns HeadBuild, or None when the scope has no docs."""
        if st.key == NET:
            if ALL in st.dirty or "daos" in st.dirty or "daos" not in st.lists:
                docs = [self._docs(s[L.DOC])[0] for s in self.idx.values() if L.DOC in s]
                st.lists["daos"] = build_list(L.NET_DAOS, docs)
            st.dirty = set()
            return build_net_head(self.N, st.lists["daos"])
        s = self.idx.get(st.key)
        if not s:
            st.lists, st.dirty = {}, set()
            return None
        full = ALL in st.dirty
        for name, spec in L.TOP_LISTS.items():
            if full or name in st.dirty or name not in st.lists:
                st.lists[name] = build_list(spec, self._docs(s.get(name, {})))
        nested_keys = {k for k in s if "/" in k}
        for k in list(st.lists):
            if "/" in k and k not in nested_keys:
                del st.lists[k]
        for k in nested_keys:
            if full or k in st.dirty or k not in st.lists:
                spec = self.place[next(iter(next(iter(s[k].values()))))].spec
                st.lists[k] = build_list(spec, self._docs(s[k]))
        st.dirty = set()
        doc = self._docs(s[L.DOC])[0] if L.DOC in s else None
        tops = {name: st.lists[name] for name in L.TOP_LISTS}
        nested = {k: st.lists[k] for k in sorted(nested_keys)}
        return build_dao_head(self.N, st.key, doc, tops, nested)

    def _objects(self, st, hb):
        objs = {}
        for lb in st.lists.values():
            objs.update(lb.objects)
        objs.update(hb.objects)
        return objs

    def _changes(self, st, pending):
        out, seen = [], set()
        for path in sorted(pending):
            place = self.place.get(path)
            if place is None or (st.key == NET and place.list_key != L.DOC):
                continue
            lp = path.lower()
            if lp in seen:
                continue
            seen.add(lp)
            group = self.idx.get(place.dao, {}).get(place.list_key, {}).get(lp)
            if group:
                out.append(self.mirror[self._pick(group)].entry)
            elif path in self.tomb:
                out.append({"path": path, "id": path.rsplit("/", 1)[1], "v": self.tomb[path], "deleted": True})
        return out

    # ---------- messages ----------

    def _split(self, entries):
        """Lists of entries, each at most MSG_MAX serialized; None when a resync is needed."""
        raws = []
        for e in entries:
            r = canonical(e)
            if len(r) > ENTRY_STALE:
                e = {"path": e["path"], "id": e["id"], "v": e["v"], "stale": True}
                r = canonical(e)
            raws.append((e, r))
        if sum(len(r) for _, r in raws) > QUEUE_MAX_BYTES:
            return None
        overhead = 400 + 160  # channel, seq, head and counts around the changes array
        chunks, cur, size = [], [], overhead
        for e, r in raws:
            if cur and size + len(r) + 1 > MSG_MAX:
                chunks.append(cur)
                cur, size = [], overhead
            cur.append(e)
            size += len(r) + 1
        chunks.append(cur)
        return chunks

    def _messages(self, st, hb, entries, resync=False):
        chunks = None if (resync or hb is None) else self._split(entries)
        n = 1 if chunks is None else len(chunks)
        s1 = max(st.seq + 1, int(self.clock() * 1000))
        objs = []
        head = hb.hash if hb else None
        counts = hb.counts if hb else None
        for i in range(n):
            if chunks is None:
                objs.append(resync_obj(st.channel, s1 + i, head, counts))
            else:
                objs.append({"channel": st.channel, "seq": s1 + i, "kind": "changes", "head": head,
                             "counts": counts, "changes": chunks[i]})
        return objs

    # ---------- writing ----------

    def _put(self, path, data, cc):
        with_retry(self.target.put, path, data, cc)
        with self.lock:
            self.puts += 1

    def _upload(self, objs):
        with self.lock:
            todo = [(h, b) for h, b in objs.items() if h not in self.known and b is not None]
        futs = [self.up_pool.submit(self._put, L.obj_path(self.N, h), b, L.CC_IMMUTABLE) for h, b in todo]
        for f in futs:
            f.result()
        with self.lock:
            self.known.update(h for h, _ in todo)

    def _set_reach(self, st, new):
        now = self.clock()
        with self.lock:
            for h in new - st.reach:
                self.ref[h] = self.ref.get(h, 0) + 1
                self.unreach.pop(h, None)
            for h in st.reach - new:
                c = self.ref.get(h, 0) - 1
                if c <= 0:
                    self.ref.pop(h, None)
                    self.unreach[h] = now
                else:
                    self.ref[h] = c
            st.reach = set(new)

    def _write_pointer(self, st, seq, head):
        ptr = {"format": 1, "scope": st.channel, "seq": seq, "head": head}
        self._put(st.ptr_path, canonical(ptr), L.CC_POINTER)

    def _commit(self, st, hb, msg_objs):
        """Objects are uploaded: write the pointer, then enqueue."""
        seq = msg_objs[-1]["seq"]
        self._write_pointer(st, seq, hb.hash if hb else None)
        st.seq = seq
        st.head_hash = hb.hash if hb else None
        st.counts = hb.counts if hb else None
        if self.notifier is not None:
            now = self.clock()
            self.notifier.enqueue([make_msg(o, now) for o in msg_objs])
        log.info("%s: head %s seq %s (%d message%s)", st.channel, st.head_hash, seq,
                 len(msg_objs), "" if len(msg_objs) == 1 else "s")

    def _strip(self, st):
        for lb in st.lists.values():
            lb.objects = dict.fromkeys(lb.objects)

    def flush(self, key):
        st = self._scope(key)
        with st.lock:
            with self.rw.shared():
                with self.lock:
                    pending = st.pending
                    st.pending, st.first_at, st.last_at = {}, None, None
                    try:
                        hb = self._build(st)
                        entries = self._changes(st, pending)
                    except Exception:
                        st.dirty.add(ALL)
                        raise
                    objs = self._objects(st, hb) if hb else {}
                    new_reach = set(objs)
                    old_reach = set(st.reach)
                if (hb.hash if hb else None) == st.head_hash:
                    self._set_reach(st, new_reach)
                    self._strip(st)
                    return False
                self._set_reach(st, old_reach | new_reach)
                try:
                    self._upload(objs)
                    self._commit(st, hb, self._messages(st, hb, entries))
                except Exception as e:
                    with self.lock:
                        for p in pending:
                            st.pending.setdefault(p, True)
                        now = self.mono()
                        st.first_at = st.first_at if st.first_at is not None else now
                        st.last_at = st.last_at if st.last_at is not None else now
                        st.dirty.add(ALL)
                        st.retry_at = now + st.backoff
                        st.backoff = min(st.backoff * 2, 60.0)
                    self._set_reach(st, old_reach)
                    log.error("%s: flush failed (%s); retrying in %.0fs", st.channel, e, st.backoff)
                    return False
                st.backoff = 1.0
                self._set_reach(st, new_reach)
                self._strip(st)
                if hb is None:
                    with self.lock:
                        if key not in self.idx and not st.pending:
                            st.lists = {}
                return True

    def on_stale(self, channel, last_seq):
        st = next((s for s in self.scopes.values() if s.channel == channel), None)
        if st is None:
            return
        with st.lock:
            # never below a seq already allocated here: the pointer and the channel must not go back
            seq = max(last_seq + 1, st.seq + 1, int(self.clock() * 1000))
            self._write_pointer(st, seq, st.head_hash)
            st.seq = seq
            self.notifier.replace(channel, make_msg(resync_obj(channel, seq, st.head_hash, st.counts), self.clock()))

    # ---------- networks.json ----------

    def _networks_bytes(self):
        with self.lock:
            nets = [{"id": i, "data": self.contracts[i]} for i in sorted(self.contracts)]
        return canonical({"format": 1, "networks": nets})

    def flush_networks(self):
        self.networks_pending = None
        data = self._networks_bytes()
        if data != self.networks_raw:
            self._put("networks.json", data, L.CC_NETWORKS)
            self.networks_raw = data
            log.info("networks.json updated (%d networks)", len(self.contracts))

    # ---------- startup ----------

    def _read_json(self, path):
        raw = with_retry(self.target.get, path)
        return None if raw is None else json.loads(raw)

    def _old_object(self, h):
        obj = self._read_json(L.obj_path(self.N, h))
        if obj is None:
            raise Resync(f"old object {h} unreadable")
        return obj

    def _group_of(self, path):
        place = self.place.get(path)
        if place is None:
            place, _ = L.classify(path, self.C)
        if place is None:
            return None
        if place.list_key == L.DOC:
            return "daos"
        return place.list_key.rsplit("/", 1)[-1]

    def _delete_v(self, path):
        g = self._group_of(path)
        if g in self.first_read:
            return self.first_read[g]
        return max(self.first_read.values(), default=int(self.clock() * 1_000_000))

    def _diff_list(self, old_ix, new_lb, present, sets, deletes):
        """Doc-level diff of one list against its previous index (None = list absent before)."""
        hw = old_ix["hw"] if old_ix else 0
        if new_lb is not None:
            sets.extend(d.entry for d in new_lb.docs if d.v > hw)
        if old_ix:
            new_hashes = {p["h"] for p in new_lb.index["pages"]} if new_lb is not None else set()
            for p in old_ix["pages"]:
                if p["h"] in new_hashes:
                    continue
                for e in self._old_object(p["h"])["docs"]:
                    if e["path"].lower() not in present:
                        deletes[e["path"].lower()] = {"path": e["path"], "id": e["id"],
                                                      "v": self._delete_v(e["path"]), "deleted": True}

    def _startup_entries(self, st, old_head):
        present = self._present(st.key)
        sets, deletes = [], {}
        if st.key == NET:
            self._diff_list(old_head["lists"].get("daos"), st.lists["daos"], present, sets, deletes)
            return sets + list(deletes.values())
        new_doc = None
        s = self.idx.get(st.key, {})
        if L.DOC in s:
            new_doc = self._docs(s[L.DOC])[0]
        old_doc = old_head.get("doc")
        if new_doc and (not old_doc or new_doc.v > old_doc["v"]):
            sets.append(new_doc.entry)
        if old_doc and old_doc["path"].lower() not in present:
            deletes[old_doc["path"].lower()] = {"path": old_doc["path"], "id": old_doc["id"],
                                                "v": self._delete_v(old_doc["path"]), "deleted": True}
        for name in L.TOP_LISTS:
            old_ix = old_head["lists"].get(name)
            if old_ix != st.lists[name].index:
                self._diff_list(old_ix, st.lists[name], present, sets, deletes)
        old_nested = old_head.get("nested")
        if old_nested is None and old_head.get("nestedRef"):
            old_nested = self._old_object(old_head["nestedRef"])["nested"]
        old_nested = old_nested or {}
        new_nested = {k: lb for k, lb in st.lists.items() if "/" in k}
        for k in set(old_nested) | set(new_nested):
            lb = new_nested.get(k)
            old = old_nested.get(k)
            if old and lb is not None and old["ix"] == _ix_hash(lb):
                continue
            self._diff_list(self._old_object(old["ix"]) if old else None, lb, present, sets, deletes)
        return sets + list(deletes.values())

    def start_scope(self, key):
        """Startup step 3/4 for one scope."""
        st = self._scope(key)
        with st.lock, self.rw.shared():
            ptr = self._read_json(st.ptr_path)
            with self.lock:
                st.pending, st.first_at, st.last_at = {}, None, None
                st.dirty.add(ALL)
                hb = self._build(st)
                objs = self._objects(st, hb) if hb else {}
            old_head = ptr.get("head") if ptr else None
            st.seq = ptr.get("seq", 0) if ptr else 0
            st.head_hash = old_head
            new_head = hb.hash if hb else None
            if ptr is not None and old_head == new_head:
                st.counts = hb.counts if hb else None
                self._set_reach(st, set(objs))
                self._upload(objs)  # zero PUTs unless an object of the live head went missing
                self._strip(st)
                return False
            if ptr is None and hb is None:
                return False
            self._set_reach(st, set(objs))
            self._upload(objs)
            resync = hb is None
            entries = []
            if not resync and old_head is not None:
                try:
                    with self.lock:
                        entries = self._startup_entries(st, self._old_object(old_head))
                except Resync as e:
                    log.warning("%s: %s; sending resync", st.channel, e)
                    resync = True
            elif not resync:
                with self.lock:
                    entries = self._startup_entries(st, {"lists": {}, "doc": None, "nested": {}})
            self._commit(st, hb, self._messages(st, hb, entries, resync=resync))
            self._strip(st)
            return True

    def startup(self):
        """Run after every listener delivered its first snapshot."""
        t0 = self.clock()
        names = with_retry(self.target.list, f"{self.N}/o/")
        with self.lock:
            self.known = {n.rsplit("/", 1)[1][:-5] for n in names if n.endswith(".json")}
        if self.networks_index:
            self.networks_raw = with_retry(self.target.get, "networks.json")
            self.flush_networks()
        ptr_daos = set()
        for n in with_retry(self.target.list, f"{self.N}/d/"):
            parts = n.split("/")
            if len(parts) == 4 and parts[3] == "ptr.json":
                ptr_daos.add(parts[2])
        with self.lock:
            keys = [NET] + sorted(set(self.idx) | ptr_daos)
        futs = [self.flush_pool.submit(self.start_scope, k) for k in keys]
        changed = sum(1 for f in futs if f.result())
        with self.lock:
            for h in self.known:
                if h not in self.ref:
                    self.unreach.setdefault(h, t0)
        self.started = True
        self.last_gc = self.mono()
        log.info("startup: %d scopes, %d changed, %d PUTs", len(keys), changed, self.puts)
        return changed

    # ---------- GC ----------

    def gc(self, now=None):
        now = self.clock() if now is None else now
        names = with_retry(self.target.list, f"{self.N}/o/")
        deleted = 0
        with self.rw.exclusive():
            victims = []
            with self.lock:
                for n in names:
                    if not n.endswith(".json"):
                        continue
                    h = n.rsplit("/", 1)[1][:-5]
                    if h in self.ref:
                        continue
                    since = self.unreach.setdefault(h, now)
                    if now - since >= GC_GRACE:
                        victims.append((h, n))
            # parallel, so flushes (blocked while GC holds the lock) wait for seconds, not minutes
            futs = [(h, self.up_pool.submit(with_retry, self.target.delete, n)) for h, n in victims]
            for h, f in futs:
                try:
                    f.result()
                except Exception as e:
                    log.warning("gc: delete of %s failed: %s", h, e)
                    continue
                with self.lock:
                    self.known.discard(h)
                    self.unreach.pop(h, None)
                deleted += 1
        log.info("gc: %d objects listed, %d deleted", len(names), deleted)
        return deleted

    # ---------- loop ----------

    def due(self, now=None):
        now = self.mono() if now is None else now
        out = []
        with self.lock:
            for key, st in self.scopes.items():
                if not st.pending or st.flushing or now < st.retry_at:
                    continue
                if now - st.last_at >= DEBOUNCE_QUIET or now - st.first_at >= DEBOUNCE_MAX:
                    out.append(key)
        return out

    def _flush_job(self, key):
        try:
            self.flush(key)
        except Exception:
            log.exception("flush of %s crashed", key)
        finally:
            with self.lock:
                self.scopes[key].flushing = False

    def tick(self):
        now = self.mono()
        for key in self.due(now):
            with self.lock:
                self.scopes[key].flushing = True
            self.flush_pool.submit(self._flush_job, key)
        if self.networks_index and self.networks_pending is not None and now - self.networks_pending >= DEBOUNCE_QUIET:
            try:
                self.flush_networks()
            except Exception:
                log.exception("networks.json write failed")
                self.networks_pending = now
        if self.last_gc is not None and now - self.last_gc >= GC_EVERY:
            self.last_gc = now
            threading.Thread(target=self._gc_job, name="gc", daemon=True).start()

    def _gc_job(self):
        try:
            self.gc()
        except Exception:
            log.exception("gc failed")

    def run(self):
        while not self._stop:
            if self.fatal is not None:
                raise SystemExit(f"fatal: {self.fatal}")
            if self.health is not None:
                err = self.health()
                if err:
                    raise SystemExit(f"fatal: {err}")
            self.tick()
            time.sleep(0.1)

    def stop(self):
        self._stop = True


def _ix_hash(lb):
    return obj_hash(canonical(lb.index))
