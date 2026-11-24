"""Randomized safety check of layout-built traffic models (spec §8.4).

Run from the repository root:
  uv run --all-extras python benchmarks/layout_safety.py --worlds 1000 --first 100000

Each world of ``tests/intralogistics/random_worlds.py`` (seeds ``--first`` to ``--first + --worlds - 1``) is built
through the layout and run under both traffic controllers and the three routers: six runs per world. A run is one
world under one controller and one router, with ``env.run()`` and no ``until``, under the monitor of that module: an
independent body model of every vehicle, the holdings ledger, a replay of the deltas at every event, and the checks
at the end of a run that ended on its own. The wall-clock limit of each run is a failure when it is reached: a run
that is cut is never a pass.

The script prints the counts of worlds, runs and events, the runs that raised, the runs that did not return within the
limit, the monitor's violations by kind with their seeds, the known limits the monitor recognised by kind with counts
and seeds, and, per feature of the worlds, how many worlds contain it, in how many a run exercised it, and how many
runs exercised it. The exit status is 1 when a run raised, did not return or shows a violation; known limits alone
leave it 0. The wall-clock limit uses ``SIGALRM``, so the script runs on POSIX only.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import os
from pathlib import Path
import signal
import sys
import time
import traceback
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.intralogistics.random_worlds import (  # noqa: E402
    CONTROLLERS,
    FEATURES,
    ROUTERS,
    RunRecord,
    exercised,
    present,
    run_world,
    world_spec,
)


class _Timeout(Exception):
    pass


def _alarm(signum: int, frame: Any) -> None:
    raise _Timeout


def _run(spec: Any, router: str, controller: str, limit: float) -> RunRecord | tuple[str, str]:
    """The record of one run, or ``(kind, text)`` when it raised or did not return within `limit` seconds."""
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, limit)
    try:
        return run_world(spec, router=router, controller=controller)  # type: ignore[arg-type]
    except _Timeout:
        return "did not return", f"no return within {limit:g} s"
    except Exception as exc:
        return "raised", "".join(traceback.format_exception_only(exc)).strip()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def world(seed: int, limit: float) -> list[dict[str, Any]]:
    """The six runs of world `seed`; the first row also lists the features the spec contains."""
    spec = world_spec(seed)
    rows: list[dict[str, Any]] = []
    for controller in CONTROLLERS:
        for router in ROUTERS:
            found = _run(spec, router, controller, limit)
            row: dict[str, Any] = {"seed": seed, "controller": controller, "router": router}
            if isinstance(found, tuple):
                row["failed"] = found
            else:
                row["events"] = len(found.events)
                row["returned"] = found.quiescent
                row["violations"] = [(round(at, 3), kind, *rest) for at, kind, *rest in found.violations]
                row["known limits"] = [case[1] for case in found.known_limits]
                row["exercised"] = exercised(spec, found.events)
            rows.append(row)
    rows[0]["present"] = present(spec)
    return rows


def _limit(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError("the wall-clock limit must be positive: 0 would remove the bound")
    return value


def main(argv: list[str] | None = None) -> int:
    """Run the check; the exit status is 1 on an exception, a run that did not return or a violation."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worlds", type=int, default=1000)
    parser.add_argument("--first", type=int, default=100_000, help="the first seed")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2), help="worker processes")
    parser.add_argument("--limit", type=_limit, default=120.0, help="wall-clock seconds allowed to each run (positive)")
    args = parser.parse_args(argv)
    seeds = range(args.first, args.first + args.worlds)
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    if args.jobs == 1:  # in this process: no workers to start
        for seed in seeds:
            rows.extend(world(seed, args.limit))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for found in pool.map(world, seeds, [args.limit] * len(seeds), chunksize=4):
                rows.extend(found)
    failed: dict[str, list[dict[str, Any]]] = {"raised": [], "did not return": []}
    violations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    limits: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for row in rows:
        counts["runs"] += 1
        counts["events"] += row.get("events", 0)
        if "failed" in row:
            failed[row["failed"][0]].append(row)
            continue
        counts["not returned on their own"] += not row["returned"]
        counts["with violations"] += bool(row["violations"])
        counts["with a known limit"] += bool(row["known limits"])
        for violation in row["violations"]:
            violations[violation[1]].append({**row, "violation": violation})
        for kind in set(row["known limits"]):
            limits[kind].append(row)
    print(
        f"worlds {len(seeds)} (seeds {seeds.start}..{seeds.stop - 1}), runs {counts['runs']}, events {counts['events']}"
    )
    print(
        f"runs that raised {len(failed['raised'])}, "
        f"runs that did not return within the limit {len(failed['did not return'])}, "
        f"runs that ended with the queue not empty {counts['not returned on their own']}, "
        f"runs with monitor violations {counts['with violations']}, "
        f"runs with a known limit {counts['with a known limit']}"
    )
    print(f"wall-clock limit {args.limit:g} s per run, {time.perf_counter() - started:.0f} s")
    print("known limits: kind: cases, runs")
    for kind, cases in sorted(limits.items()):
        print(f"  {kind}: {sum(case['known limits'].count(kind) for case in cases)}, {len(cases)}")
        for case in cases:
            print(f"    seed {case['seed']} {case['controller']} {case['router']}: {case['known limits'].count(kind)}")
    print("feature: worlds that contain it, worlds where a run exercised it, runs that exercised it")
    for feature in FEATURES:
        contain = sum(feature in row.get("present", ()) for row in rows)
        runs = [row for row in rows if feature in row.get("exercised", ())]
        print(f"  {feature}: {contain}, {len({row['seed'] for row in runs})}, {len(runs)}")
    for kind, cases in sorted(failed.items()):
        for case in cases:
            print(f"{kind.upper()} seed {case['seed']} {case['controller']} {case['router']}: {case['failed'][1]}")
    for kind, cases in sorted(violations.items()):
        print(f"VIOLATION {kind!r}: {len({(c['seed'], c['controller'], c['router']) for c in cases})} runs")
        for case in cases:
            print(f"  seed {case['seed']} {case['controller']} {case['router']}: {case['violation']!r}")
    for row in rows:
        if "returned" in row and not row["returned"]:
            print(f"NOT RETURNED seed {row['seed']} {row['controller']} {row['router']}")
    bad = any(failed.values()) or bool(violations) or any("returned" in row and not row["returned"] for row in rows)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
