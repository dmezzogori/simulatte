"""Sampling cost: a router-only workload in the installed simulatte version (spec §14, T14).

A ``Scenario.pure_job_shop`` router (10 servers, utilization 0.9, the builder's default distributions) generates
exactly ``--jobs`` jobs into a sink that only counts them, so no job is processed: the run measures job generation,
that is sampling (stream binding and samplers on the branch, the global ``random`` module in 0.12.0), job
construction and the router's SimPy timeouts. The sampler calls per job are fixed by the routing: inter-arrival
time, SKU, routing, one processing time per operation and the due-date offset (``4 + len(routing)``).

Usage::

    python benchmarks/bench_sampling.py --jobs 20000 --warmup 2 --repeat 10 --json sampling.json

The JSON has the keys of ``run.py`` that ``compare.py`` needs (mode ``sampling``, never gated) plus ``jobs_per_s``
and ``draws_per_s`` (sampler calls per second).
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import inspect
import json
import platform
import random
import sys
import time
from pathlib import Path
from typing import Any

import feeder
from run import _version, provenance, quartiles
from simulatte.environment import Environment
from simulatte.scenario import Scenario

SERVERS = 10
UTILIZATION = 0.9
SEED = 1


class Sink:
    """Stands in for the pre-shop pool: counts the generated jobs and their operations, then drops them."""

    def __init__(self, env: Any, jobs: int) -> None:
        self.jobs = 0
        self.operations = 0
        self._target = jobs
        self.done = env.event()

    def add(self, job: Any) -> None:
        self.jobs += 1
        self.operations += len(job.routing)
        if self.jobs == self._target:
            self.done.succeed()


def run_once(jobs: int) -> tuple[float, Sink]:
    gc.collect()
    start = time.perf_counter()
    if "seed" in inspect.signature(Environment).parameters:
        env: Any = Environment(seed=SEED)
    else:
        random.seed(SEED)  # 0.12.0 samples from the global random module
        env = Environment()
    scenario = Scenario.pure_job_shop(n_servers=SERVERS, target_utilization=UTILIZATION)
    shop_floor, servers = scenario.build_floor(env)
    sink = Sink(env, jobs)
    scenario.build_router(env, shop_floor, servers, psp=sink)  # ty: ignore[invalid-argument-type]  # duck-typed pool
    env.run(until=sink.done)
    env.close()
    return time.perf_counter() - start, sink


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jobs", type=int, required=True)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--json", help="write the results here (default: stdout)")
    parser.add_argument("--label", help="name of the measured version in reports")
    args = parser.parse_args(argv)
    samples = []
    draws = []
    for i in range(args.warmup + args.repeat):
        wall, sink = run_once(args.jobs)
        if i >= args.warmup:
            samples.append(wall)
            draws.append(4 * sink.jobs + sink.operations)
        print(f"{'warmup' if i < args.warmup else 'run'} {i + 1}: {wall:.4f} s", file=sys.stderr)
    q1, median, q3 = quartiles(samples)
    mean_draws = sum(draws) / len(draws)
    spec = f"sampling servers={SERVERS} util={UTILIZATION} jobs={args.jobs}"
    out = {
        "label": args.label or feeder.default_label(),
        "simulatte_version": _version(),
        "mode": "sampling",
        "python": {
            "implementation": sys.implementation.name,
            "version": platform.python_version(),
            "build": platform.python_build()[0],
        },
        "platform": {"system": platform.system(), "machine": platform.machine(), "node": platform.node()},
        **provenance(),
        # compare.py checks that both sides ran the same workload and job count.
        "workload": {"path": "router-only", "sha256": hashlib.sha256(spec.encode()).hexdigest(), "spec": spec},
        "counts": {"jobs": args.jobs},
        "draws_mean": mean_draws,
        "warmup": args.warmup,
        "repeat": args.repeat,
        "samples_s": samples,
        "median_s": median,
        "iqr_s": q3 - q1,
        "min_s": min(samples),
        "max_s": max(samples),
        "jobs_per_s": args.jobs / median,
        "draws_per_s": mean_draws / median,
    }
    text = json.dumps(out, indent=2)
    if args.json:
        Path(args.json).write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
