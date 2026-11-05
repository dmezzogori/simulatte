"""Serve a fixed recording through the installed viewer with optional read latency."""

import argparse
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform

import uvicorn

from simulatte.studio.server import ViewerServer, assets


def main(args):
    static = assets()
    viewer = ViewerServer(args.trace, args.port, static)

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/trace" and scope["method"] == "GET":
            await asyncio.sleep(args.delay_ms / 1000)
        await viewer.app(scope, receive, send)

    owns_access = False
    try:
        args.metadata.write_text(
            json.dumps(
                dict(
                    trace_sha256=hashlib.sha256(args.trace.read_bytes()).hexdigest(),
                    assets_sha256=hashlib.sha256((static / "manifest.json").read_bytes()).hexdigest(),
                    platform=platform.platform(),
                    python=platform.python_version(),
                    versions={name: version(name) for name in ("simulatte", "starlette", "uvicorn")},
                    delay_ms=args.delay_ms,
                ),
                indent=2,
            )
            + "\n"
        )
        fd = os.open(args.access, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        owns_access = True
        with os.fdopen(fd, "w") as file:
            json.dump(
                dict(
                    origin=viewer.origin,
                    secret=viewer.secret,
                    cookie_name=viewer.cookie_name,
                    cookie=viewer.cookie,
                    pid=os.getpid(),
                ),
                file,
            )
        uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False, log_level="error")).run()
    finally:
        viewer.close()
        if owns_access:
            args.access.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--access", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--delay-ms", type=float, default=5)
    main(parser.parse_args())
