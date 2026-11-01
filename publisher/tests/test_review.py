import json

from publisher.core import Publisher
from publisher.notify import Notifier, make_msg
from publisher.tests.helpers import *  # noqa: F401,F403


def _msg(ch, seq, at=0.0):
    return make_msg({"channel": ch, "seq": seq, "kind": "changes", "head": "h", "counts": {}, "changes": []}, at)


def test_unknown_result_status_backs_off_instead_of_spinning():
    clock = Clock(100.0)
    nf = Notifier("http://x/notify", "s", clock=clock,
                  post=lambda u, b, h: (200, json.dumps({"results": [{"status": "weird"}]}).encode()))
    nf.enqueue([_msg("net:Localhost", 5, 100.0)])
    assert nf.step() is False
    assert nf.next_try > clock.t and nf.pending() == 1


def test_malformed_results_do_not_raise():
    nf = Notifier("http://x/notify", "s", clock=Clock(100.0),
                  post=lambda u, b, h: (200, b'{"results":["x"]}'))
    nf.enqueue([_msg("net:Localhost", 5, 100.0)])
    assert nf.step() is False


def test_stale_for_a_seq_we_posted_counts_as_duplicate():
    ch = "net:Localhost"
    calls = []
    replies = [None, {"results": [{"channel": ch, "status": "stale", "lastSeq": 7}]}]

    def post(u, b, h):
        r = replies[len(calls)]
        calls.append(json.loads(b)["messages"])
        if r is None:
            raise ConnectionError("response lost after the Worker applied it")
        return 200, json.dumps(r).encode()

    stale = []
    nf = Notifier("http://x/notify", "s", post=post, clock=Clock(100.0), on_stale=lambda c, s: stale.append(s))
    nf.enqueue([_msg(ch, 5, 100.0), _msg(ch, 6, 100.0), _msg(ch, 7, 100.0)])
    assert nf.step() is False
    nf.next_try = 0
    # pretend the retry was packed with only the first message
    nf._batch = lambda: [nf.queues[ch][0]]
    assert nf.step() is True
    assert stale == [] and nf.pending() == 0


def test_genuine_stale_still_resyncs():
    ch = "net:Localhost"
    stale = []
    nf = Notifier("http://x/notify", "s", clock=Clock(100.0), on_stale=lambda c, s: stale.append(s),
                  post=lambda u, b, h: (200, json.dumps(
                      {"results": [{"channel": ch, "status": "stale", "lastSeq": 99}]}).encode()))
    nf.enqueue([_msg(ch, 5, 100.0)])
    nf.step()
    assert stale == [99]


def test_on_stale_never_moves_seq_back():
    pub, t, nt = make_pub()
    seed_basic(pub)
    pub.startup()
    st = pub.scopes[D]
    st.seq = int(pub.clock() * 1000) + 10 ** 6   # allocated ahead of the clock
    before = st.seq
    pub.on_stale(st.channel, 1)
    assert st.seq == before + 1 == t.json(f"{N}/d/{D}/ptr.json")["seq"]
    assert nt.replaced[-1]["seq"] == st.seq


def test_restart_reuploads_missing_object_of_unchanged_head():
    pub, t, nt = make_pub()
    seed_basic(pub)
    pub.startup()
    ptr = t.json(f"{N}/d/{D}/ptr.json")
    head = t.json(f"{N}/o/{ptr['head']}.json")
    page = head["lists"]["proposals"]["pages"][0]["h"]
    del t.files[f"{N}/o/{page}.json"]
    pub2, _, nt2 = make_pub(target=t)
    seed_basic(pub2)
    pub2.startup()
    assert f"{N}/o/{page}.json" in t.files and nt2.msgs == []


def test_enqueue_collapses_old_queue_while_backing_off():
    clock = Clock(100.0)
    nf = Notifier("http://x/notify", "s", clock=clock, post=lambda u, b, h: (503, b""))
    ch = "net:Localhost"
    nf.enqueue([_msg(ch, 1, 100.0)])
    clock.t += 61
    nf.enqueue([_msg(ch, 2, clock.t)])
    q = nf.queues[ch]
    assert len(q) == 1 and q[0].obj["kind"] == "resync" and q[0].seq == 2


def test_listener_death_is_fatal():
    pub, t, nt = make_pub()
    pub.health = lambda: "listener votes stopped"
    try:
        pub.run()
    except SystemExit as e:
        assert "votes" in str(e)
    else:
        raise AssertionError("run() kept going with a dead listener")
