"""Lazy fixed-size admission batches; run retention modes in separate processes."""

import argparse
import json
from pathlib import Path
import resource
import sys

from simulatte.experimental.native import Job, NativeStream


def main(retention, output):
    rows = []
    with NativeStream(3, capacities=(3, 1, 2), policy="spt", wip_cap=5, retention=retention) as stream:
        for batch in range(4096):
            for i in range(16):
                stream.add(Job(batch * 32, (i % 3, (i + 1) % 3), (0.5, 0.25), batch * 32 + 6))
            stream.advance(batch * 32 + 32)
            if batch + 1 in (1, 8, 64, 256, 1024, 4096):
                stats = stream.storage_stats()
                rows.append(
                    dict(
                        jobs=(batch + 1) * 16,
                        storage=stats,
                        snapshot=stream.snapshot(),
                        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                        * (1 if sys.platform == "darwin" else 1024),
                    )
                )
        stream.run()
        assert stream.storage_stats()["completed"] == 65536
        if retention == "bounded":
            assert all(row["storage"]["retained_jobs"] == 0 for row in rows)
            assert len({row["storage"]["native_bytes"] for row in rows[1:]}) == 1
        output.write_text(json.dumps(dict(retention=retention, rows=rows, final=stream.snapshot()), indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retention", choices=("full", "bounded"), required=True)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    main(args.retention, args.output)
