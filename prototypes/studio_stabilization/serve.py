"""Wrap the installed Studio gate with private, prefix-only ASGI diagnostics."""

import argparse
import asyncio
import importlib.util
from itertools import count
import json
from pathlib import Path
import re
import time


def main(args):
    # Reuse the original study's real Studio setup, model and resource sampling.
    spec = importlib.util.spec_from_file_location("studio_study_base", args.work / "serve.py")
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    original = base.server_config
    ids = count(1)
    args.output = args.output.resolve()

    def config(app):
        async def observe(scope, receive, send):
            path = scope.get("path", "")
            if scope["type"] != "http" or not re.fullmatch(r"/api/prefix/[0-9a-f]{32}/[0-9]+", path):
                return await app(scope, receive, send)
            headers = dict(scope["headers"])
            client = headers.get(b"x-studio-study-client", b"").decode("ascii", errors="replace")
            interval = headers.get(b"range", b"").decode("ascii", errors="replace")
            tag = headers.get(b"x-studio-study-fetch", b"").decode("ascii", errors="replace")
            row = dict(
                id=next(ids),
                at=time.time_ns() / 1e6,
                method=scope["method"],
                path=path,
                client=client if re.fullmatch(r"[a-z0-9-]{1,40}", client) else None,
                range=interval if re.fullmatch(r"bytes=[0-9]*-[0-9]*", interval) else None,
                tag=tag if re.fullmatch(r"[0-9a-f-]{36}-[0-9]{1,9}", tag) else None,
                status=None,
                expected=None,
                sent=0,
                ended=False,
                error=None,
                disconnected=False,
            )

            async def received():
                message = await receive()
                row["disconnected"] |= message["type"] == "http.disconnect"
                return message

            async def sent(message):
                if message["type"] == "http.response.start":
                    row["status"] = message["status"]
                    length = dict(message["headers"]).get(b"content-length")
                    row["expected"] = int(length) if length is not None else None
                    row["headers_at"] = time.time_ns() / 1e6
                    message = {
                        **message,
                        "headers": [*message["headers"], (b"x-studio-study-read", str(row["id"]).encode())],
                    }
                    await send(message)
                    if row["client"] == "cancel-probe" and row["status"] == 206 and scope["method"] == "GET":
                        await asyncio.sleep(args.header_gap_ms / 1000)
                    return
                await send(message)
                if message["type"] == "http.response.body":
                    row["sent"] += len(message.get("body", b""))
                    row["ended"] = not message.get("more_body", False)

            try:
                if args.latency_ms:
                    await asyncio.sleep(args.latency_ms / 1000)
                await app(scope, received, sent)
            except BaseException as error:
                row["error"] = type(error).__name__
                raise
            finally:
                row["end"] = time.time_ns() / 1e6
                with (args.output / "reads.jsonl").open("a") as file:
                    file.write(json.dumps(row, separators=(",", ":")) + "\n")

        return original(observe)

    base.server_config = config
    base.main(args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pace", type=float, default=0.006)
    parser.add_argument("--latency-ms", type=float, default=0)
    parser.add_argument("--header-gap-ms", type=float, default=1000)
    main(parser.parse_args())
