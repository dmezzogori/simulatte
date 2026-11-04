"""Real loopback Studio server; private access data never enters evidence."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import psutil
import uvicorn

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.snapshot import discover_project
from simulatte.studio.app import StudioServer
from simulatte.studio.command import server_config
from simulatte.studio.server import assets


def main(args):
    work, output = args.work.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    coordinator = Coordinator(discover_project(work / "project/plant.py", work / "project"))
    static = assets()
    studio = StudioServer(
        coordinator,
        args.port,
        static,
        timeout=180,
        configure=lambda _: dict(
            level="kpi",
            parameters=dict(
                case="flow",
                engine="native",
                retention="bounded",
                policy="spt",
                jobs=16000,
                budget=1000,
                pace=args.pace,
                fault=False,
            ),
        ),
    )
    config = server_config(studio.app)
    config.host, config.port, config.log_level = "127.0.0.1", args.port, "error"
    server = uvicorn.Server(config)
    access = work / "access.json"
    with open(access, "w", opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
        json.dump(
            dict(
                url=studio.link(),
                secret=studio.secret,
                origin=studio.origin,
                pid=os.getpid(),
                cookie_name=studio.cookie_name,
                cookie=studio.cookie,
            ),
            file,
        )
    (output / "server.json").write_text(
        json.dumps(
            dict(
                pid=os.getpid(),
                port=args.port,
                assets_sha256=hashlib.sha256((static / "manifest.json").read_bytes()).hexdigest(),
                pace=args.pace,
                started=time.time(),
            ),
            indent=2,
        )
        + "\n"
    )
    stop = threading.Event()

    def sample():
        process = psutil.Process()
        with (output / "resources.jsonl").open("w", buffering=1) as file:
            while not stop.wait(5):
                children = []
                for child in process.children(recursive=True):
                    try:
                        children.append(dict(pid=child.pid, rss=child.memory_info().rss, fds=child.num_fds()))
                    except psutil.Error:
                        pass
                with coordinator.store._transaction() as db:
                    counts = dict(db.execute("SELECT status,COUNT(*) FROM attempts GROUP BY status").fetchall())
                    results = db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
                disk = sum(p.stat().st_size for p in (work / "project/.simulatte").rglob("*") if p.is_file())
                file.write(
                    json.dumps(
                        dict(
                            at=time.time(),
                            rss=process.memory_info().rss,
                            fds=process.num_fds(),
                            children=children,
                            attempts=counts,
                            results=results,
                            store_bytes=disk,
                        )
                    )
                    + "\n"
                )
                if (work / "stop").exists():
                    server.should_exit = True
                    return

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        server.run()
    finally:
        stop.set()
        thread.join(timeout=10)
        coordinator.close()
        access.unlink(missing_ok=True)
    print(json.dumps(dict(stopped=True, output=str(output))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pace", type=float, default=0.006)
    main(parser.parse_args())
