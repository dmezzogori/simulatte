"""A loopback-only capability server for one immutable recorded file."""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import stat
import sys
import threading
import webbrowser

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response, StreamingResponse
from starlette.routing import Route
import uvicorn

BUILD_COMMAND = "pnpm --dir studio build && uv run python studio/scripts/package_assets.py"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def assets() -> Path:
    directory = Path(__file__).with_name("static")
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        from importlib.metadata import version

        if manifest["version"] != version("simulatte"):
            raise ValueError("version")
        for name, digest in manifest["assets"].items():
            path = directory / name
            if (
                not path.resolve().is_relative_to(directory.resolve())
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("hash")
        # Editable checkout: check source freshness using the standalone build hook without importing hatchling.
        root = directory.parents[3]
        if (root / "studio").is_dir():
            digest = hashlib.sha256()
            excluded = {"node_modules", "dist", "test-results", "playwright-report", ".vite", ".build"}
            inputs = [root / "src/simulatte/layout/schema/layout-v1.schema.json", *(root / "studio").rglob("*")]
            for path in sorted(inputs, key=lambda p: p.as_posix()):
                relative = path.relative_to(root)
                if path.is_file() and not excluded.intersection(relative.parts):
                    digest.update(relative.as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
            if digest.hexdigest() != manifest["source_fingerprint"]:
                raise ValueError("source")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(f"Studio assets missing or stale. Run {BUILD_COMMAND}") from error
    return directory


class ViewerServer:
    def __init__(self, trace: Path, port: int, static: Path):
        self.path = trace.absolute()
        try:
            before = self.path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise OSError("Not a regular file")
            self.fd = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0),
            )
        except OSError as error:
            raise ValueError("Trace must be a readable regular file, not a symlink") from error
        self.identity = os.fstat(self.fd)
        if not stat.S_ISREG(self.identity.st_mode) or (before.st_dev, before.st_ino) != (
            self.identity.st_dev,
            self.identity.st_ino,
        ):
            os.close(self.fd)
            raise ValueError("Trace must be a readable regular file")
        self.origin = f"http://127.0.0.1:{port}"
        self.host = f"127.0.0.1:{port}"
        self.secret = secrets.token_urlsafe(32)
        self.cookie_name = "simulatte-" + secrets.token_hex(8)
        self.cookie = secrets.token_urlsafe(32)
        self.token = ""
        self.lock = threading.Lock()
        self.static = static
        self.allowed = set(json.loads((static / "manifest.json").read_text())["assets"])
        self.etag = '"' + secrets.token_hex(32) + '"'
        self.app = Starlette(routes=[Route("/{path:path}", self.request, methods=["GET", "HEAD"])])

    def link(self) -> str:
        with self.lock:
            self.token = secrets.token_urlsafe(32)
            return f"{self.origin}/bootstrap?token={self.token}"

    def unchanged(self) -> bool:
        try:
            current = os.stat(self.path, follow_symlinks=False)
            opened = os.fstat(self.fd)
            keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            return all(getattr(current, key) == getattr(self.identity, key) == getattr(opened, key) for key in keys)
        except OSError:
            return False

    async def request(self, request: Request) -> Response:
        headers = {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": CSP,
        }

        def response(body: str, status: int = 400) -> Response:
            return Response(body, status_code=status, headers=headers)

        if request.headers.get("host") != self.host or len(request.headers.getlist("host")) != 1:
            return response("Invalid host", 403)
        origin = request.headers.get("origin")
        if origin is not None and origin != self.origin:
            return response("Invalid origin", 403)
        path = request.path_params["path"]
        if path == "bootstrap":
            token = request.query_params.get("token", "")
            with self.lock:
                accepted = bool(self.token and hmac.compare_digest(token.encode(), self.token.encode()))
                if accepted and request.method == "GET":
                    self.token = ""
                else:
                    accepted = False
            if not accepted:
                return response(
                    "Link unavailable. Enter link in the serving terminal, or restart with --no-browser.", 403
                )
            nonce = secrets.token_urlsafe(24)
            headers["Content-Security-Policy"] = (
                f"default-src 'none'; script-src 'nonce-{nonce}'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            )
            body = (
                f'<!doctype html><meta charset="utf-8"><title>Simulatte</title><p id="message">Opening viewer…</p>'
                f'<script nonce="{nonce}">try {{const key={json.dumps(self.secret)};'
                'sessionStorage.setItem("simulatte-session",key);'
                'if(sessionStorage.getItem("simulatte-session")!==key)throw Error();'
                'location.replace("/");}catch{document.getElementById("message").textContent='
                '"Browser storage unavailable. Enable session storage, then enter link in the serving terminal '
                'or restart with --no-browser.";}</script>'
            )
            result = HTMLResponse(body, headers=headers)
            result.set_cookie(self.cookie_name, self.cookie, httponly=True, samesite="strict", path="/")
            return result
        if request.url.query:
            return response("Unknown resource", 404)
        if path == "trace":
            if not (
                hmac.compare_digest(request.cookies.get(self.cookie_name, "").encode(), self.cookie.encode())
                and hmac.compare_digest(request.headers.get("x-simulatte-session", "").encode(), self.secret.encode())
            ):
                return response("Authentication required. Open a fresh terminal link.", 403)
            if not self.unchanged():
                return response("Recording changed. Close the viewer and reopen the trace.", 409)
            if request.headers.get("if-match", self.etag) != self.etag:
                return response("Recording identity changed", 412)
            size = self.identity.st_size
            start, end, status = 0, size - 1, 200
            raw_range = request.headers.get("range")
            if raw_range:
                try:
                    if not raw_range.startswith("bytes=") or "," in raw_range:
                        raise ValueError
                    first, last = raw_range[6:].split("-")
                    if first:
                        start = int(first)
                        end = min(int(last), size - 1) if last else size - 1
                    else:
                        suffix = int(last)
                        if suffix <= 0:
                            raise ValueError
                        start = max(0, size - suffix)
                    if start < 0 or start > end or start >= size:
                        raise ValueError
                except ValueError:
                    headers["Content-Range"] = f"bytes */{size}"
                    return response("Invalid range", 416)
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers.update(
                {
                    "Content-Length": str(max(0, end - start + 1)),
                    "Accept-Ranges": "bytes",
                    "ETag": self.etag,
                    "Content-Type": "application/octet-stream",
                }
            )
            if request.method == "HEAD":
                return Response(status_code=status, headers=headers)

            def stream():
                offset = start
                while offset <= end:
                    if not self.unchanged():
                        raise RuntimeError("Recording changed; reopen the trace")
                    size = min(1024 * 1024, end - offset + 1)
                    if hasattr(os, "pread"):
                        data = os.pread(self.fd, size, offset)
                    else:
                        with self.lock:
                            os.lseek(self.fd, offset, os.SEEK_SET)
                            data = os.read(self.fd, size)
                    if not data or not self.unchanged():
                        raise RuntimeError("Recording changed; reopen the trace")
                    offset += len(data)
                    yield data

            return StreamingResponse(stream(), status_code=status, headers=headers)
        name = path or "index.html"
        if name not in self.allowed or ".." in Path(name).parts:
            return response("Unknown resource", 404)
        asset = self.static / name
        if not asset.resolve().is_relative_to(self.static.resolve()):
            return response("Unknown resource", 404)
        data = asset.read_bytes()
        headers["Content-Length"] = str(len(data))
        return Response(
            data if request.method == "GET" else b"",
            media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            headers=headers,
        )

    def close(self) -> None:
        os.close(self.fd)


def serve(trace: str, *, port: int = 0, no_browser: bool = False) -> None:
    static = assets()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        viewer = ViewerServer(Path(trace), sock.getsockname()[1], static)
        try:
            print(f"Simulatte viewer: {viewer.origin}\nCtrl-C to stop. Enter link for a fresh one-time browser link.")
            link = viewer.link()
            if no_browser:
                print(link)
            elif not webbrowser.open(link):
                print("Browser could not open. Enter link below, or restart with --no-browser.")

            def terminal():
                for line in sys.stdin:
                    if line.strip() == "link":
                        print(viewer.link(), flush=True)

            if sys.stdin.isatty():
                threading.Thread(target=terminal, daemon=True).start()
            # Access logs must never persist bootstrap credentials. No model modules are imported here.
            uvicorn.Server(uvicorn.Config(viewer.app, access_log=False, log_level="critical")).run(sockets=[sock])
        finally:
            viewer.close()
