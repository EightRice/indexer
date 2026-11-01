"""Dev static server for a dir: target (CONTRACT.md section 11).

python -m publisher.serve --dir C:\\code\\DAO\\local-edge --port 8788
"""
import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .layout import CC_IMMUTABLE


def make_handler(root):
    root = os.path.abspath(root)

    class Handler(BaseHTTPRequestHandler):
        server_version = "publisher-serve"

        def _resolve(self):
            path = unquote(urlsplit(self.path).path).lstrip("/")
            full = os.path.abspath(os.path.join(root, *[p for p in path.split("/") if p]))
            if not full.startswith(root + os.sep) or not os.path.isfile(full):
                return path, None
            return path, full

        def _send(self, head_only):
            path, full = self._resolve()
            if full is None:
                self.send_response(404)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", CC_IMMUTABLE if "/o/" in "/" + path else "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head_only:
                self.wfile.write(data)

        def do_GET(self):
            self._send(False)

        def do_HEAD(self):
            self._send(True)

        def _refuse(self):
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

        def log_message(self, fmt, *args):
            pass

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m publisher.serve")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--port", type=int, default=8788)
    args = ap.parse_args(argv)
    os.makedirs(args.dir, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.dir))
    print(f"serving {os.path.abspath(args.dir)} on http://127.0.0.1:{args.port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
