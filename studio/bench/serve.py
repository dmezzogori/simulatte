"""Isolated local acceptance harness; never shipped as the product server."""

from __future__ import annotations
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil

parser = argparse.ArgumentParser()
parser.add_argument("trace", type=Path)
parser.add_argument("--port", type=int, default=4180)
parser.add_argument("--output", type=Path, default=Path("/tmp/sp3-browser"))
args = parser.parse_args()
root = Path(__file__).with_name("dist")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(root), **kw)

    def do_GET(self):
        if self.path == "/fixture.simtrace":
            self.send_response(200)
            self.send_header("Content-Length", str(args.trace.stat().st_size))
            self.end_headers()
            with args.trace.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path not in {"/artifact", "/result"}:
            self.send_error(404)
            return
        target = args.output.with_suffix(".bin" if self.path == "/artifact" else ".json")
        remaining = int(self.headers["Content-Length"])
        with target.open("wb") as f:
            while remaining:
                part = self.rfile.read(min(remaining, 1024 * 1024))
                if not part:
                    raise RuntimeError("Truncated upload")
                f.write(part)
                remaining -= len(part)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


print(f"Acceptance harness on 127.0.0.1:{args.port}", flush=True)
ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
