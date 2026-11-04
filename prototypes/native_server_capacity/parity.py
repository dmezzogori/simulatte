"""Broader production parity sweep, including non-binary processing times."""

import argparse
import hashlib
import json
from pathlib import Path
import random
import struct

from simulatte.experimental.native import Job, NativeStream
from tests.experimental.test_native_capacity import PythonPlant


def main(output):
    rows = []
    for seed in range(16):
        for policy in ("fifo", "spt"):
            for cap in (None, 5):
                for nonbinary in (False, True):
                    rng = random.Random(seed)
                    jobs = tuple(
                        Job(
                            i // 3 * 0.5,
                            tuple(rng.sample(range(3), 3)),
                            tuple(
                                rng.uniform(0.1, 2) if nonbinary else rng.choice((0.25, 0.5, 1, 2)) for _ in range(3)
                            ),
                            float(rng.randrange(2, 25)),
                        )
                        for i in range(48)
                    )
                    capacities = (3, 1, 2)
                    oracle = PythonPlant(jobs, capacities, policy, cap, 0.3)
                    try:
                        with (
                            NativeStream(3, capacities=capacities, policy=policy, wip_cap=cap, warmup=0.3) as full,
                            NativeStream(
                                3, capacities=capacities, policy=policy, wip_cap=cap, warmup=0.3, retention="bounded"
                            ) as bounded,
                        ):
                            for job in jobs:
                                full.add(job)
                                bounded.add(job)
                            for at in (0.3, 0.5, 1, 2, 4, 8, 16, 40, 200):
                                expected = oracle.advance(at)
                                for stream in (full, bounded):
                                    stream.advance(at)
                                    assert stream.snapshot()["scalars"] == expected, (seed, policy, cap, nonbinary, at)
                                    raw = stream.state_bytes()
                                    if stream.retention == "bounded":
                                        assert raw[:8] == b"NRST0002"
                                        raw, offset, width, occupied = raw[48:], 100, 32, 4
                                        assert raw[:8] == b"NBST0002"
                                    else:
                                        offset, width, occupied = 108, 16, 12
                                        assert raw[:8] == b"NSTA0002"
                                    assert struct.unpack_from("<I", raw, 8)[0] == 3
                                    for i, server in enumerate(oracle.servers):
                                        assert struct.unpack_from("<I", raw, offset + i * width)[0] == capacities[i]
                                        assert (
                                            struct.unpack_from("<I", raw, offset + i * width + occupied)[0]
                                            == server.count
                                        )
                            full.run()
                            bounded.run()
                            expected = oracle.result()
                            for name, values in expected.items():
                                assert full.result()[name] == values, (seed, policy, cap, nonbinary, name)
                            assert full.snapshot() == bounded.snapshot()
                            rows.append(
                                dict(
                                    seed=seed,
                                    policy=policy,
                                    cap=cap,
                                    nonbinary=nonbinary,
                                    state_sha256=hashlib.sha256(full.state_bytes()).hexdigest(),
                                    bounded_sha256=hashlib.sha256(bounded.state_bytes()).hexdigest(),
                                    trajectory_sha256=hashlib.sha256(
                                        json.dumps(expected, sort_keys=True).encode()
                                    ).hexdigest(),
                                )
                            )
                    finally:
                        oracle.env.close()
    output.write_text(json.dumps(dict(passed=True, cases=len(rows), rows=rows), indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    main(parser.parse_args().output)
