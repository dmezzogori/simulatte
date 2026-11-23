"""Parity of explicit and layout models: the same random worlds built both ways must run alike (spec §8.1).

Run from the repository root:
  uv run --all-extras python benchmarks/layout_parity.py --worlds 1000

Each world of ``tests/intralogistics/random_worlds.py`` (seeds ``--first`` to ``--first + --worlds - 1``) is built
with concrete graph objects and through ``Explicit`` and layout handles, and run under both traffic controllers and
the three routers: twelve runs and six compared pairs per world. A pair is equal when the two `RunRecord`s are: the
events after the prelude (without ``seq`` and ``ordinal``), the initial and final states, the end time, whether the
run ended on its own, and the monitor's violations. The script prints the counts, then for each differing pair its
seed and first difference, and the seeds of runs that raised, did not end within the wall-clock limit, or show a
monitor violation, with what each mode did.

`first_difference` and `compare` are importable: they compare two recorded streams, or two run records.
This script exists while explicit mode does.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
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
    ROUTERS,
    RunRecord,
    StreamRecorder,
    normalize,
    run_world,
    world_spec,
)

__all__ = ["StreamRecorder", "compare", "first_difference", "normalize"]


def first_difference(a: Sequence[Any], b: Sequence[Any]) -> str | None:
    """None when the two normalized streams are equal, else the index and both sides of their first difference."""
    for index, (left, right) in enumerate(zip(a, b)):
        if left != right:
            return f"event {index}:\n  explicit: {left!r}\n  layout:   {right!r}"
    if len(a) != len(b):
        index = min(len(a), len(b))
        extra = a[index] if len(a) > index else b[index]
        side = "explicit" if len(a) > index else "layout"
        return f"event {index}: only {side} has more ({len(a)} against {len(b)} events), first {extra!r}"
    return None


def compare(a: RunRecord, b: RunRecord) -> str | None:
    """None when two run records are equal, else their first difference: events first, then the other fields."""
    found = first_difference(a.events, b.events)
    if found is not None:
        return found
    for name in ("initial_state", "final_state", "now", "quiescent", "violations"):
        left, right = getattr(a, name), getattr(b, name)
        if left != right:
            if isinstance(left, dict) and isinstance(right, dict):
                key = next(k for k in sorted(set(left) | set(right)) if left.get(k) != right.get(k))
                return f"{name}[{key!r}]:\n  explicit: {left.get(key)!r}\n  layout:   {right.get(key)!r}"
            return f"{name}:\n  explicit: {left!r}\n  layout:   {right!r}"
    return None


COUNTS = (
    "equal",
    "differing",
    "events compared",
    "failed in both modes",
    "ended at the horizon",
    "with monitor violations",
)


class _Timeout(Exception):
    pass


def _alarm(signum: int, frame: Any) -> None:
    raise _Timeout


def _run(spec: Any, mode: str, router: str, controller: str, until: float, limit: int) -> RunRecord | str:
    """The record of one run, or a short text when it raised or did not end within `limit` seconds."""
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(limit)
    try:
        return run_world(spec, mode=mode, router=router, controller=controller, until=until)  # type: ignore[arg-type]
    except _Timeout:
        return f"did not end within {limit} s"
    except Exception as exc:
        return "raised " + "".join(traceback.format_exception_only(exc)).strip()
    finally:
        signal.alarm(0)


def world(seed: int, until: float, limit: int) -> list[dict[str, Any]]:
    """The six compared pairs of world `seed`."""
    spec = world_spec(seed)
    rows = []
    for controller in CONTROLLERS:
        for router in ROUTERS:
            explicit = _run(spec, "explicit", router, controller, until, limit)
            layout = _run(spec, "layout", router, controller, until, limit)
            row: dict[str, Any] = {"seed": seed, "controller": controller, "router": router}
            if isinstance(explicit, str) or isinstance(layout, str):
                row["failed"] = (
                    explicit if isinstance(explicit, str) else "ran",
                    layout if isinstance(layout, str) else "ran",
                )
                row["difference"] = None if explicit == layout else "one mode failed differently"
            else:
                row["difference"] = compare(explicit, layout)
                row["events"] = len(explicit.events)
                row["quiescent"] = explicit.quiescent and layout.quiescent
                row["violations"] = (explicit.violations[:3], layout.violations[:3])
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worlds", type=int, default=1000)
    parser.add_argument("--first", type=int, default=0, help="the first seed")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--until", type=float, default=20_000.0, help="simulated horizon of each run")
    parser.add_argument("--limit", type=int, default=120, help="wall-clock seconds allowed to each run")
    args = parser.parse_args()
    seeds = range(args.first, args.first + args.worlds)
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for found in pool.map(world, seeds, [args.until] * len(seeds), [args.limit] * len(seeds), chunksize=4):
            rows.extend(found)
    counts = Counter()
    for row in rows:
        counts["pairs"] += 1
        counts["equal" if row["difference"] is None else "differing"] += 1
        counts["events compared"] += 2 * row.get("events", 0)
        counts["failed in both modes"] += "failed" in row and row["difference"] is None
        counts["ended at the horizon"] += row.get("quiescent") is False
        counts["with monitor violations"] += bool(row.get("violations") and any(row["violations"]))
    print(f"worlds {len(seeds)} (seeds {seeds.start}..{seeds.stop - 1}), runs {2 * len(rows)}, pairs {len(rows)}")
    print(", ".join(f"{name} {counts[name]}" for name in COUNTS))
    print(f"until {args.until}, wall-clock limit {args.limit} s per run, {time.perf_counter() - started:.0f} s")
    for row in rows:
        tag = f"seed {row['seed']} {row['controller']} {row['router']}"
        if row["difference"] is not None:
            print(f"DIFFERS {tag}: {row['difference']}")
        if "failed" in row:
            print(f"FAILED {tag}: explicit {row['failed'][0]!r}; layout {row['failed'][1]!r}")
        elif row.get("quiescent") is False:
            print(f"HORIZON {tag}: the run did not end on its own before {args.until}")
        if row.get("violations") and any(row["violations"]):
            print(f"VIOLATION {tag}: explicit {row['violations'][0]!r}; layout {row['violations'][1]!r}")


if __name__ == "__main__":
    main()
