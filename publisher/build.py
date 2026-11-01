"""Pages, list indexes, views and heads (CONTRACT.md sections 4.3 to 4.7). Pure functions."""
from dataclasses import dataclass, field

from .encoding import canonical, obj_hash
from .layout import PAGE_MAX, VIEW_MAX, HEAD_MAX, ID_ORDER


@dataclass
class Doc:
    path: str
    id: str
    v: int
    k: object
    entry: dict      # the DocEntry
    raw: bytes       # canonical bytes of the DocEntry

    @property
    def lid(self):
        return self.id.lower()

    @property
    def sk(self):
        return (self.k, self.lid)


@dataclass
class ListBuild:
    index: dict
    objects: dict = field(default_factory=dict)   # hash -> bytes (pages and views)
    docs: list = field(default_factory=list)      # ascending

    @property
    def hashes(self):
        return set(self.objects)


def _page_bytes(coll, docs):
    return b'{"coll":' + canonical(coll) + b',"docs":[' + b",".join(d.raw for d in docs) + b"]}"


def _rank_split(coll, docs, P):
    """Greedy pages from the oldest doc: at most P docs and PAGE_MAX bytes (one big doc alone)."""
    overhead = len(_page_bytes(coll, []))
    pages, cur, size = [], [], overhead
    for d in docs:
        add = len(d.raw) + (1 if cur else 0)
        if cur and (len(cur) >= P or size + add > PAGE_MAX):
            pages.append(cur)
            cur, size, add = [], overhead, len(d.raw)
        cur.append(d)
        size += add
    if cur:
        pages.append(cur)
    return pages


def _store(objects, data):
    h = obj_hash(data)
    objects[h] = data
    return h


def _prefix_len(count):
    return 0 if count <= 400 else 1 if count <= 6400 else 2


def build_list(spec, docs):
    """docs: deduplicated Doc list in any order."""
    docs = sorted(docs, key=lambda d: d.sk)
    objects = {}
    pages = []
    groups = []  # (prefix or None, [docs])
    if spec.part == "prefix":
        L = _prefix_len(len(docs))
        buckets = {}
        for d in docs:
            buckets.setdefault(d.lid[2:2 + L], []).append(d)
        for p in sorted(buckets):
            chunks = _rank_split(spec.coll, buckets[p], 10 ** 9)
            for chunk in chunks:
                groups.append((p, chunk, len(chunks) > 1))
    else:
        for chunk in _rank_split(spec.coll, docs, spec.P):
            groups.append((None, chunk, True))
    for p, chunk, ranged in groups:
        h = _store(objects, _page_bytes(spec.coll, chunk))
        e = {"h": h, "n": len(chunk)}
        if p is not None:
            e["p"] = p
        if ranged:
            e["lo"] = [chunk[0].k, chunk[0].lid]
            e["hi"] = [chunk[-1].k, chunk[-1].lid]
        pages.append(e)
    index = {
        "coll": spec.coll,
        "count": len(docs),
        "hw": max((d.v for d in docs), default=0),
        "order": {"key": spec.key, "dir": spec.dir},
        "part": spec.part,
        "pages": pages,
        "views": {},
    }
    for view in spec.views:
        index["views"][view] = _store(objects, _view(view, spec, docs, groups))
    return ListBuild(index, objects, docs)


def _view(view, spec, docs, groups):
    if view == "search":
        items = []
        for d in docs:
            data = d.entry["data"]
            eco = data.get("economy")
            items.append({"id": d.id, "name": data.get("name"), "symbol": data.get("symbol"),
                          "economy": bool(eco) if isinstance(eco, str) else False, "k": d.k})
        return canonical({"items": items})
    if view == "loc":
        loc = {}
        for i, (_, chunk, _) in enumerate(groups):
            for d in chunk:
                loc[d.lid] = i
        return canonical({"loc": loc})
    if view == "queued":
        q = [d for d in docs if d.entry["data"].get("latestStage") == "Queued"]
        data = _page_bytes(spec.coll, q)
        if len(data) > VIEW_MAX:
            data = canonical({"coll": spec.coll, "ids": [d.id for d in q]})
        return data
    if view == "top":
        ranked = []
        for d in docs:
            data = d.entry["data"]
            try:
                bal = int(data.get("personalBalance"))
            except (TypeError, ValueError):
                continue
            if bal > 0:
                ranked.append((-bal, d.lid, d, data))
        ranked.sort(key=lambda t: (t[0], t[1]))
        return canonical({"items": [{"id": d.id, "address": data.get("address") or d.id,
                                     "personalBalance": data.get("personalBalance")}
                                    for _, _, d, data in ranked[:100]]})
    raise ValueError(view)


@dataclass
class HeadBuild:
    hash: str
    data: bytes
    objects: dict        # head-level objects: head, nested ixs, nestedRef
    counts: dict


def build_net_head(N, daos):
    head = {"format": 1, "scope": f"net:{N}", "lists": {"daos": daos.index}}
    data = canonical(head)
    h = obj_hash(data)
    return HeadBuild(h, data, {h: data}, {"daos": daos.index["count"]})


def build_dao_head(N, d, doc, tops, nested):
    """doc: Doc or None; tops: {name: ListBuild}; nested: {key: ListBuild} (non-empty only)."""
    objects = {}
    nmap = {}
    for key in sorted(nested):
        ix = canonical(nested[key].index)
        nmap[key] = {"count": nested[key].index["count"], "ix": _store(objects, ix)}
    head = {"format": 1, "scope": f"dao:{N}:{d}", "doc": doc.entry if doc else None,
            "lists": {name: b.index for name, b in tops.items()}, "nested": nmap}
    data = canonical(head)
    if len(data) > HEAD_MAX:
        del head["nested"]
        head["nestedRef"] = _store(objects, canonical({"nested": nmap}))
        data = canonical(head)
    h = _store(objects, data)
    return HeadBuild(h, data, objects, {name: b.index["count"] for name, b in tops.items()})
