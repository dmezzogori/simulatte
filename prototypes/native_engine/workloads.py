"""Generate once on CPython and share the exact JSON bytes across backends."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from .model import Job, Workload

ROOT = Path(__file__).resolve().parent


def generate(count, load, seed=20261015):
    rng, arrival, jobs = random.Random(seed), 0.0, []
    for _ in range(count):
        arrival += rng.expovariate(10 * load / 5.5)
        route = rng.sample(range(10), rng.randint(1, 10))
        durations = [rng.uniform(0.5, 1.5) for _ in route]
        jobs.append(Job(arrival, tuple(route), tuple(durations), arrival + rng.uniform(30, 45)))
    return Workload(10, tuple(jobs))


def save(workload, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(
        servers=workload.servers,
        jobs=[dict(arrival=j.arrival, route=j.route, durations=j.durations, due=j.due) for j in workload.jobs],
    )
    path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")


def read(path):
    payload = json.loads(Path(path).read_text())
    return Workload(payload["servers"], tuple(Job(**job) for job in payload["jobs"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, nargs="+", default=[5000, 50000])
    args = parser.parse_args()
    for count in args.jobs:
        for load in (0.5, 0.95):
            path = ROOT / ".inputs" / f"jobs-{count}-load-{load}.json"
            save(generate(count, load), path)
            print(path)


if __name__ == "__main__":
    main()
