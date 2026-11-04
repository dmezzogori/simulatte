"""Sample browser process-tree RSS during the latter part of the timed sessions."""

import argparse
import json
from pathlib import Path
import time

import psutil


def main(args):
    deadline = time.monotonic() + args.seconds
    with args.output.open("w", buffering=1) as file:
        while time.monotonic() < deadline:
            for host in ("macos", "linux"):
                logs = args.results / f"studio-browser-final-{host}/browser.jsonl"
                current = {}
                for line in logs.read_text().splitlines():
                    row = json.loads(line)
                    if row["type"] == "sample":
                        current[row["engine"]] = row["browser_pid"]
                for engine, pid in current.items():
                    try:
                        process = psutil.Process(pid)
                        children = process.children(recursive=True)
                        values = []
                        for child in (process, *children):
                            try:
                                values.append(dict(pid=child.pid, rss=child.memory_info().rss, fds=child.num_fds()))
                            except psutil.Error:
                                pass
                        file.write(
                            json.dumps(
                                dict(
                                    at=time.time(),
                                    host=host,
                                    engine=engine,
                                    processes=values,
                                    rss=sum(row["rss"] for row in values),
                                )
                            )
                            + "\n"
                        )
                    except psutil.NoSuchProcess:
                        pass
            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=600)
    main(parser.parse_args())
