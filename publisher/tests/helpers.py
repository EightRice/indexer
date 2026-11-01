import json
from datetime import datetime, timezone

from publisher.core import Publisher

N = "Localhost"
C = "idaosLocalhost"
DAO = "0x5FbDB2315678afecb367f032d93F642f64180aa3"  # checksummed
D = DAO.lower()
VOTER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


class FakeTarget:
    def __init__(self):
        self.files = {}
        self.log = []  # ("put"|"delete", path)

    def get(self, path):
        return self.files.get(path)

    def put(self, path, data, cc):
        self.files[path] = data
        self.log.append(("put", path))

    def list(self, prefix):
        return [p for p in self.files if p.startswith(prefix)]

    def delete(self, path):
        self.files.pop(path, None)
        self.log.append(("delete", path))

    def json(self, path):
        return json.loads(self.files[path])

    def puts(self):
        return [p for k, p in self.log if k == "put"]


class FakeNotifier:
    """Records messages and checks that pointer and objects exist when a message is enqueued."""

    def __init__(self, target, n=N):
        self.target, self.n = target, n
        self.msgs = []
        self.on_stale = None
        self.replaced = []

    def enqueue(self, msgs):
        for m in msgs:
            o = m.obj
            if o["head"] is not None:
                assert f"{self.n}/o/{o['head']}.json" in self.target.files, "notify before head object"
            self.msgs.append(o)

    def replace(self, channel, msg):
        self.replaced.append(msg.obj)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def make_pub(target=None, notifier=True, **kw):
    target = target or FakeTarget()
    clock = kw.pop("clock", Clock())
    mono = kw.pop("mono", Clock(1000.0))
    nt = FakeNotifier(target) if notifier else None
    pub = Publisher(N, C, target, notifier=nt, clock=clock, mono=mono, **kw)
    pub.first_read = {g: 1_799_999_999_000_000 for g in
                      ("daos", "proposals", "votes", "members", "activity", "projects", "backers")}
    return pub, target, nt


def dao_doc(n_props=1):
    return {"name": "Test DAO", "symbol": "TST", "decimals": 18, "creationDate": ts("2026-10-13T12:00:00"),
            "token": "0x" + "1" * 40, "registryAddress": "0x" + "2" * 40, "treasuryAddress": "0x" + "3" * 40,
            "address": DAO, "proposals": [str(i) for i in range(n_props)], "registry": {}, "treasury": {},
            "votingDelay": 1, "votingDuration": 5, "quorum": 4, "economy": None}


def proposal_doc(i, stage="pending", created="2026-10-14T10:00:00"):
    return {"hash": "0x" + "ab" * 32, "type": "transfer", "title": f"P{i}", "description": "d",
            "author": VOTER, "createdAt": ts(created), "latestStage": stage,
            "statusHistory": {"pending": ts(created)}, "callDatas": [b"\xde\xad\xbe\xef"],
            "inFavor": "0", "against": "0", "votesFor": 0, "votesAgainst": 0, "votesCast": 0}


def member_doc(addr, bal, voted=()):
    return {"address": addr, "delegate": addr, "personalBalance": str(bal), "votingWeight": str(bal),
            "constituents": [], "proposalsVoted": list(voted), "proposalsCreated": [],
            "balanceSort": float(bal), "lastSeen": ts("2026-10-14T10:00:00")}


def vote_doc(voter, weight, cast="2026-10-14T11:00:00+00:00"):
    return {"voter": voter, "weight": str(weight), "option": 1, "cast": cast, "reason": "", "hash": "0x" + "cd" * 32}


def seed_basic(pub):
    """A DAO with one proposal, one member; versions start at 1_000_000."""
    pub.apply(f"{C}/{DAO}", 1_000_001, dao_doc(), 1)
    pub.apply(f"{C}/{DAO}/proposals/0", 1_000_002, proposal_doc(0), 1)
    pub.apply(f"{C}/{DAO}/members/{VOTER}", 1_000_003, member_doc(VOTER, 10 ** 18), 1)
