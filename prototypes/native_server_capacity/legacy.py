"""Unit-state evidence, run with both previous and new installed companions."""

import hashlib
import json
from pathlib import Path
import random
import sys

from simulatte._wire import canonical_pack, freeze
from simulatte.experimental.native import Job, NativeStream


def digest(value):
    return hashlib.sha256(canonical_pack(freeze(value))).hexdigest()


def main(output):
    rows = []
    for retention in ("full", "bounded"):
        for policy in ("fifo", "spt"):
            for cap in (None, 3):
                rng = random.Random(71)
                with NativeStream(3, retention=retention, policy=policy, wip_cap=cap, warmup=2) as stream:
                    for i in range(96):
                        stream.add(
                            Job(
                                i // 4 * 0.5,
                                tuple(rng.sample(range(3), 3)),
                                tuple(rng.choice((0.25, 0.5, 1, 2)) for _ in range(3)),
                                rng.randrange(3, 40),
                            )
                        )
                    for at in (0, 0.25, 1, 2, 4, 10, 40, 100, 400):
                        stream.advance(at)
                        rows.append(
                            dict(
                                retention=retention,
                                policy=policy,
                                cap=cap,
                                at=at,
                                raw=hashlib.sha256(stream.state_bytes()).hexdigest(),
                                projection=digest(stream.state_projection()),
                                snapshot=stream.snapshot(),
                            )
                        )
                    stream.run()
                    rows.append(
                        dict(
                            retention=retention,
                            policy=policy,
                            cap=cap,
                            at="run",
                            raw=hashlib.sha256(stream.state_bytes()).hexdigest(),
                            projection=digest(stream.state_projection()),
                            snapshot=stream.snapshot(),
                            result=None if retention == "bounded" else digest(stream.result()),
                        )
                    )
    output.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
