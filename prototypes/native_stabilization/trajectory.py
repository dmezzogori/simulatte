"""Independent production-actor starts/ends/dispatch plus live streaming parity."""

import argparse
import hashlib
import json
from pathlib import Path

from simulatte import Environment
from simulatte.experimental.native import Job, NativeStream
from workloads import Admission, CASES, PythonPlant, specification


def main(output):
    rows = []
    for case, config in CASES.items():
        for policy in ("fifo", "spt"):
            for seed in (3, 21, 77):
                for warmup in (0, 64):
                    env = Environment(seed=0)
                    env.configure_kpis(warmup=warmup)
                    oracle = PythonPlant(env, case, policy)
                    env.activate()
                    control = Admission(case, seed, 600)
                    try:
                        with (
                            NativeStream(
                                len(config.capacities),
                                capacities=config.capacities,
                                policy=policy,
                                wip_cap=config.wip_cap,
                                warmup=warmup,
                            ) as full,
                            NativeStream(
                                len(config.capacities),
                                capacities=config.capacities,
                                policy=policy,
                                wip_cap=config.wip_cap,
                                warmup=warmup,
                                retention="bounded",
                            ) as bounded,
                        ):
                            for tick in range(257):
                                if tick > env.now:
                                    env.run(until=tick)
                                for native in (full, bounded):
                                    native.advance(tick)
                                    stats = native.storage_stats()
                                    assert (stats["admitted"], stats["completed"]) == oracle.counts(), (
                                        case,
                                        policy,
                                        seed,
                                        tick,
                                    )
                                    assert native.snapshot()["scalars"] == oracle.kpis(), (
                                        case,
                                        policy,
                                        seed,
                                        warmup,
                                        tick,
                                    )
                                if tick == 256:
                                    break
                                admitted, completed = oracle.counts()
                                for _ in range(control.offer(admitted - completed)):
                                    spec = specification(case, seed, control.cursor)
                                    job = Job(tick, spec["route"], spec["durations"], spec["due"])
                                    oracle.add(job)
                                    full.add(job)
                                    bounded.add(job)
                                    control.cursor += 1
                                control.tick += 1
                            env.run()
                            full.run()
                            bounded.run()
                            expected, actual = oracle.result(), full.result()
                            for key, values in expected.items():
                                assert actual[key] == values, (case, policy, seed, warmup, key)
                            assert full.snapshot() == bounded.snapshot()
                            assert full.snapshot()["scalars"] == oracle.kpis()
                            rows.append(
                                dict(
                                    case=case,
                                    policy=policy,
                                    seed=seed,
                                    warmup=warmup,
                                    admitted=control.cursor,
                                    control=control.inputs(),
                                    trajectory=hashlib.sha256(
                                        json.dumps(expected, sort_keys=True).encode()
                                    ).hexdigest(),
                                )
                            )
                    finally:
                        env.close()
    output.write_text(json.dumps(dict(passed=True, cases=len(rows), rows=rows), indent=2) + "\n")
    print(json.dumps(dict(passed=True, cases=len(rows))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    main(parser.parse_args().output)
