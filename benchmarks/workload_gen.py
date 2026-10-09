"""Generate a pre-generated job-shop workload for the overhead benchmarks (spec §14).

The workload is a list of jobs (arrival time, SKU, routing as server indices, processing times, due date) drawn
once from a seeded ``random.Random`` and written as JSON, so that every simulatte version replays exactly the same
jobs through :mod:`feeder` without drawing any random number itself.

The shop mirrors the defaults of ``Scenario.pure_job_shop``: a pure job shop (routing length uniform in
``[1, servers]``, distinct servers in random order), truncated 2-Erlang processing times (rate 2, truncated at 4),
Poisson arrivals at the rate that yields the target utilization, due date = arrival + Uniform(30, 45).

Usage::

    python benchmarks/workload_gen.py --servers 10 --jobs 5000 --util 0.9 --seed 1 > workload.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from typing import Any

FORMAT = "simulatte-bench-workload-v1"

SERVICE_RATE = 2.0
SERVICE_SHAPE = 2
SERVICE_MAX = 4.0
DUE_LOW = 30.0
DUE_HIGH = 45.0
DECIMALS = 6


def _erlang_cdf(shape: int, rate: float, x: float) -> float:
    """CDF of an Erlang(shape, rate) distribution at `x`."""
    term = 1.0
    total = 1.0
    for n in range(1, shape):
        term *= rate * x / n
        total += term
    return 1.0 - math.exp(-rate * x) * total


def truncated_erlang_mean(shape: int, rate: float, max_value: float) -> float:
    """``E[X | X <= max_value]`` for an Erlang(shape, rate) variable (as ``TruncatedErlang.mean``)."""
    return (shape / rate) * _erlang_cdf(shape + 1, rate, max_value) / _erlang_cdf(shape, rate, max_value)


def generate(*, servers: int, jobs: int, util: float, seed: int, drain: float) -> dict[str, Any]:
    """Draw the workload; times are rounded to `DECIMALS` places so the JSON stays small."""
    if servers < 1 or jobs < 1:
        raise ValueError("servers and jobs must be positive")
    if not 0 < util <= 1:
        raise ValueError("util must be in (0, 1]")
    rng = random.Random(seed)
    mean_pt = truncated_erlang_mean(SERVICE_SHAPE, SERVICE_RATE, SERVICE_MAX)
    rate = util * servers / ((servers + 1) / 2 * mean_pt)
    indices = list(range(servers))
    rows: list[list[Any]] = []
    now = 0.0
    for _ in range(jobs):
        now += rng.expovariate(rate)
        arrival = round(now, DECIMALS)
        routing = rng.sample(indices, k=rng.randint(1, servers))
        times = []
        for _ in routing:
            while True:
                sample = sum(rng.expovariate(SERVICE_RATE) for _ in range(SERVICE_SHAPE))
                if sample <= SERVICE_MAX:
                    break
            times.append(round(sample, DECIMALS))
        due = round(arrival + rng.uniform(DUE_LOW, DUE_HIGH), DECIMALS)
        rows.append([arrival, "F1", routing, times, due])
    return {
        "format": FORMAT,
        "params": {
            "servers": servers,
            "jobs": jobs,
            "util": util,
            "seed": seed,
            "drain": drain,
            "arrival_rate": rate,
            "service": {"dist": "truncated_erlang", "rate": SERVICE_RATE, "shape": SERVICE_SHAPE, "max": SERVICE_MAX},
            "due_offset": {"dist": "uniform", "low": DUE_LOW, "high": DUE_HIGH},
            "generator": sys.implementation.name,
        },
        "servers": servers,
        "operations": sum(len(row[2]) for row in rows),
        "horizon": round(rows[-1][0] + drain, DECIMALS),
        "jobs": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--servers", type=int, default=10)
    parser.add_argument("--jobs", type=int, required=True)
    parser.add_argument("--util", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--drain",
        type=float,
        default=1000.0,
        help="time after the last arrival at which the run stops; every job must be finished by then",
    )
    args = parser.parse_args(argv)
    workload = generate(servers=args.servers, jobs=args.jobs, util=args.util, seed=args.seed, drain=args.drain)
    json.dump(workload, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
