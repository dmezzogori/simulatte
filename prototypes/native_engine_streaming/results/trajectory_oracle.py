"""Reconstruct completion commitments from unchanged packaged trajectory arrays."""

from pathlib import Path
import hashlib
import json
import random
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from simulatte.experimental.native import Job, NativeStream
from prototypes.native_engine_streaming.stream import Stream

passed = 0
for policy in ("fifo", "spt"):
    for cap in (None, 2):
        for seed in range(4):
            rng = random.Random(seed)
            jobs = []
            for i in range(35):
                count = rng.randrange(1, 4)
                jobs.append(
                    Job(
                        i // 3 * 0.5,
                        tuple(rng.sample(range(3), count)),
                        tuple(rng.choice((0.25, 0.5, 1.2)) for _ in range(count)),
                        rng.randrange(3, 20),
                    )
                )
            with (
                NativeStream(3, policy=policy, wip_cap=cap) as old,
                Stream(3, policy=policy, wip_cap=cap, retention="bounded") as new,
            ):
                for job in jobs:
                    old.add(job)
                    new.add(job)
                for t in (1, 2, 4, 6, 8):
                    old.advance(t)
                    new.advance(t)
                old.run()
                new.run()
                result = old.result()
                offsets = [0]
                for job in jobs:
                    offsets.append(offsets[-1] + len(job.route))
                rank = {op: i for i, op in enumerate(result["dispatch"])}
                order = sorted(range(len(jobs)), key=lambda j: (result["finished"][j], rank[offsets[j + 1] - 1]))
                history = hashlib.sha256()
                for j in order:
                    job = jobs[j]
                    history.update(
                        struct.pack(
                            "<QddddI",
                            j,
                            job.arrival,
                            job.due,
                            result["released"][j],
                            result["finished"][j],
                            len(job.route),
                        )
                    )
                    for k, (server, duration) in enumerate(zip(job.route, job.durations)):
                        op = offsets[j] + k
                        queued = result["released"][j] if k == 0 else result["ends"][op - 1]
                        history.update(
                            struct.pack("<Idddd", server, duration, queued, result["starts"][op], result["ends"][op])
                        )
                assert history.hexdigest() == new.history_digest, (policy, cap, seed)
                assert old.snapshot() == new.snapshot()
                passed += 1
print(json.dumps(dict(passed=passed, exact_packaged_trajectory_commitments=True)))
