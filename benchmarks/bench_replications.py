"""Measure complete Runner throughput, including build, extraction, close and process startup."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from functools import partial
from pathlib import Path
from typing import Any

from simulatte import Runner
from simulatte.builders import build_immediate_release_system
from simulatte.scenario import Scenario


def build(*, env: Any, servers: int) -> Any:
    return build_immediate_release_system(env=env, scenario=Scenario(n_servers=servers))


def extract(system: Any) -> dict[str, Any]:
    return {
        "completed": len(system.shop_floor.jobs_done),
        "finish_times": [job.finished_at for job in system.shop_floor.jobs_done],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replications", type=int, default=8)
    parser.add_argument("--horizon", type=float, default=1000)
    parser.add_argument("--servers", type=int, default=6)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if min(args.replications, args.servers, args.workers, args.repeat) < 1 or args.horizon <= 0:
        parser.error("counts and horizon must be positive")
    output: dict[str, Any] = {"parameters": vars(args) | {"json": None}, "modes": {}}
    reference = None
    for parallel in (False, True):
        samples = []
        for _ in range(args.repeat):
            start = time.perf_counter()
            runner = Runner(
                builder=partial(build, servers=args.servers),
                seeds=list(range(args.replications)),
                parallel=parallel,
                n_jobs=args.workers,
                progress=False,
                extract_fn=extract,
            )
            results = runner.run(until=args.horizon)
            samples.append(time.perf_counter() - start)
            fingerprint = hashlib.sha256(json.dumps(results, sort_keys=True).encode()).hexdigest()
            if reference is None:
                reference = fingerprint
            if fingerprint != reference:
                raise RuntimeError("replication results differ between repeated/sequential/parallel runs")
        median = statistics.median(samples)
        output["modes"]["parallel" if parallel else "sequential"] = {
            "samples_s": samples,
            "median_s": median,
            "replications_per_s": args.replications / median,
            "trajectory": reference,
        }
    encoded = json.dumps(output, indent=2) + "\n"
    if args.json:
        args.json.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
