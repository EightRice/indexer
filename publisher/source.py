"""Firestore listeners feeding the publisher (CONTRACT.md section 5.1)."""
import logging
import threading

from .encoding import dt_to_us

log = logging.getLogger("publisher.source")

GROUPS = ("proposals", "votes", "members", "activity", "projects", "backers")


def init_db(cred_path):
    """Same convention as app.py: service account file, FIRESTORE_EMULATOR_HOST set beforehand."""
    from firebase_admin import initialize_app, firestore, credentials
    app = initialize_app(credentials.Certificate(cred_path), name="publisher")
    return firestore.client(app=app)


class Listener:
    def __init__(self, name, query, on_change, prefix=None):
        self.name, self.query, self.on_change, self.prefix = name, query, on_change, prefix
        self.first = threading.Event()
        self.first_read = None
        self.watch = None

    def _cb(self, docs, changes, read_time):
        try:
            rt = dt_to_us(read_time)
            for ch in changes:
                doc = ch.document
                path = doc.reference.path
                if self.prefix and not path.startswith(self.prefix):
                    continue
                if ch.type.name == "REMOVED":
                    self.on_change(path, None, None, rt)
                else:
                    self.on_change(path, dt_to_us(doc.update_time), doc.to_dict(), rt)
            if not self.first.is_set():
                self.first_read = rt
                self.first.set()
                log.info("listener %s: first snapshot, %d docs", self.name, len(docs))
        except Exception:
            log.exception("listener %s callback failed", self.name)

    def start(self):
        self.watch = self.query.on_snapshot(self._cb)
        return self

    def dead(self):
        """True once the watch has shut down for good (an unrecoverable stream error)."""
        return self.watch is not None and getattr(self.watch, "_closed", False)

    def stop(self):
        if self.watch is not None:
            try:
                self.watch.unsubscribe()
            except Exception:
                pass


def bounded_group(db, C, g):
    from google.cloud.firestore_v1.field_path import FieldPath
    from google.cloud.firestore_v1.base_query import FieldFilter
    low = db.document(f"{C}/\u0000/{g}/\u0000")
    high = db.document(f"{C}/￿/{g}/￿")
    return (db.collection_group(g)
            .where(filter=FieldFilter(FieldPath.document_id(), ">=", low))
            .where(filter=FieldFilter(FieldPath.document_id(), "<=", high)))


def attach(db, C, publisher, networks_index, timeout=120):
    """Attach every listener and wait for each first snapshot. Returns the listeners."""
    listeners = []

    def contract_change(path, v, data, rt):
        publisher.apply_contract(path.rsplit("/", 1)[1], data)

    if networks_index:
        listeners.append(Listener("contracts", db.collection("contracts"), contract_change))
    listeners.append(Listener("daos", db.collection(C), publisher.apply))
    for g in GROUPS:
        q = bounded_group(db, C, g)
        try:
            q.limit(1).get()  # a missing index override fails here, before the watch
            listeners.append(Listener(g, q, publisher.apply))
        except Exception as e:
            log.warning("bounded %s listener unavailable (%s); using the whole group", g, e)
            listeners.append(Listener(g, db.collection_group(g), publisher.apply, prefix=C + "/"))
    for li in listeners:
        li.start()
    for li in listeners:
        if not li.first.wait(timeout):
            raise TimeoutError(f"listener {li.name} delivered no snapshot in {timeout}s")
        if li.name != "contracts":
            publisher.first_read[li.name] = li.first_read
    return listeners
