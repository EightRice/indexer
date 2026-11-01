import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from publisher.serve import make_handler


def test_serve_headers(tmp_path):
    (tmp_path / "N" / "o").mkdir(parents=True)
    (tmp_path / "N" / "o" / "abc.json").write_bytes(b"{}")
    (tmp_path / "N" / "ptr.json").write_bytes(b'{"seq":1}')
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        r = urllib.request.urlopen(base + "/N/o/abc.json?x=1")
        assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert r.headers["Access-Control-Allow-Origin"] == "*"
        assert r.headers["Content-Type"] == "application/json; charset=utf-8"
        assert urllib.request.urlopen(base + "/N/ptr.json").headers["Cache-Control"] == "no-store"
        for path in ("/N/o/missing.json", "/../x"):
            try:
                urllib.request.urlopen(base + path)
                assert False
            except urllib.error.HTTPError as e:
                assert e.code == 404 and e.headers["Cache-Control"] == "no-store"
        try:
            urllib.request.urlopen(urllib.request.Request(base + "/N/ptr.json", data=b"x", method="POST"))
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 405
    finally:
        srv.shutdown()
