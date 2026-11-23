"""What a layout costs a production model: construction, preparation, activation, the run (spec §9 of
``specs/2026-11-23-layout-only-models-design.md``).

Run from the repository root:
  uv run --all-extras python benchmarks/layout_overhead.py --repeat 9

Every measurement builds a fresh environment, runs ``gc.collect()`` outside the timing and times one step with
``time.perf_counter``; it is repeated ``--repeat`` times after one warm-up, and the script prints the median and the
minimum of the repetitions in milliseconds. The cases:

- ``construct``: ``Environment()`` already created, then ``Layout(env)`` (in the ``layout`` variant) and ``n``
  single-capacity servers; the ``layout`` variant is also reported as a ratio to the ``none`` variant.
- ``prepare``: ``layout.prepare()`` on a model of ``n`` servers built with ``Layout(env)``.
- ``activate``: ``env.activate()`` on the same model (it prepares the layout), and on the model without a layout.
- ``first_bundle``: the first read of ``prepare().bundle.full_hash`` after ``prepare()``.
- ``jobs_before_layout``: 10 servers and 1,600 production jobs created first, then ``Layout(env)`` and
  ``layout.prepare()`` timed together.
- ``run``: a 10-server immediate-release pure job shop (``Scenario.pure_job_shop(n_servers=10)``, no default
  metrics, no subscriber, seed 7) built, activated outside the timing and run to a horizon of about 30,000 domain
  events (counted once, in a separate run with a counting subscriber), with and without ``Layout(env)``; reported
  also as nanoseconds per domain event and as a ratio.

Nobody observes these runs: no recorder, no digest, no manifest read (L14). ``--json`` writes the results with the
commit, the interpreter and the machine.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import gc
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from simulatte import Environment, Layout
from simulatte.builders import build_immediate_release_system
from simulatte.events import DomainEvent
from simulatte.job import ProductionJob
from simulatte.scenario import Scenario
from simulatte.server import Server

ROOT = Path(__file__).resolve().parents[1]
HORIZON = 450.0
"""Horizon of the shop run: about 30,000 domain events with seed 7."""


def _timed(setup: Callable[[], Any], measure: Callable[[Any], object], repeat: int) -> list[float]:
    """Milliseconds of `measure(setup())`, once as a warm-up and then `repeat` times."""
    times = []
    for index in range(repeat + 1):
        state = setup()
        gc.collect()
        started = time.perf_counter()
        measure(state)
        elapsed = (time.perf_counter() - started) * 1000
        if index:
            times.append(elapsed)
    return times


def _servers(env: Environment, n: int) -> None:
    for _ in range(n):
        Server(env=env, capacity=1)


def _construct(n: int, with_layout: bool) -> Callable[[Environment], None]:
    def measure(env: Environment) -> None:
        if with_layout:
            Layout(env)
        _servers(env, n)

    return measure


def _model(n: int, with_layout: bool) -> Environment:
    env = Environment()
    if with_layout:
        Layout(env)
    _servers(env, n)
    return env


def _jobs_model() -> Environment:
    env = Environment()
    servers = [Server(env=env, capacity=1) for _ in range(10)]
    for index in range(1_600):
        route = [servers[index % 10], servers[(index + 3) % 10]]
        ProductionJob(env=env, sku="A", servers=route, processing_times=[1.0, 1.0], due_date=100.0)
    return env


def _jobs_measure(env: Environment) -> None:
    Layout(env).prepare()


def _shop(with_layout: bool) -> Environment:
    env = Environment(seed=7)
    if with_layout:
        Layout(env)
    build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=10), default_metrics=False)
    env.activate()
    return env


def _count_events() -> int:
    env = _shop(False)
    count = 0

    def handler(_: DomainEvent) -> None:
        nonlocal count
        count += 1

    env.bus.subscribe(handler, "*")
    env.run(until=HORIZON)
    return count


def _summary(times: list[float]) -> dict[str, float]:
    return {"median_ms": statistics.median(times), "min_ms": min(times)}


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def measure(repeat: int) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for n in (100, 1_600):
        none = _summary(_timed(Environment, _construct(n, False), repeat))
        layout = _summary(_timed(Environment, _construct(n, True), repeat))
        results[f"construct/{n}/none"] = none
        results[f"construct/{n}/layout"] = layout
        results[f"construct/{n}/ratio"] = layout["median_ms"] / none["median_ms"]
    for n in (10, 1_600):
        results[f"prepare/{n}"] = _summary(
            _timed(lambda n=n: _model(n, True), lambda env: env.layout.prepare(), repeat)
        )
        results[f"activate/{n}/layout"] = _summary(
            _timed(lambda n=n: _model(n, True), lambda env: env.activate(), repeat)
        )
        results[f"activate/{n}/none"] = _summary(
            _timed(lambda n=n: _model(n, False), lambda env: env.activate(), repeat)
        )

        def prepared(n: int = n) -> Any:
            env = _model(n, True)
            return env.layout.prepare()

        results[f"first_bundle/{n}"] = _summary(_timed(prepared, lambda result: result.bundle.full_hash, repeat))
    results["jobs_before_layout/1600"] = _summary(_timed(_jobs_model, _jobs_measure, repeat))
    events = _count_events()
    results["run/events"] = events
    for variant, with_layout in (("none", False), ("layout", True)):
        summary = _summary(_timed(lambda w=with_layout: _shop(w), lambda env: env.run(until=HORIZON), repeat))
        summary["median_ns_per_event"] = summary["median_ms"] * 1e6 / events
        results[f"run/{variant}"] = summary
    results["run/ratio"] = results["run/layout"]["median_ms"] / results["run/none"]["median_ms"]
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=9, help="timed repetitions per case, after one warm-up")
    parser.add_argument("--json", type=Path, help="write the results as JSON")
    args = parser.parse_args(argv)
    results = measure(args.repeat)
    meta = {
        "commit": _commit(),
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "machine": platform.machine(),
        "processor": platform.processor(),
        "repeat": args.repeat,
    }
    print(f"commit {meta['commit']}, {meta['python']}, {meta['machine']}, repeat {args.repeat} (median / min, ms)")
    for name, value in results.items():
        if isinstance(value, dict):
            text = f"{value['median_ms']:10.3f} / {value['min_ms']:10.3f}"
            if "median_ns_per_event" in value:
                text += f"   {value['median_ns_per_event']:.1f} ns per event (median)"
        elif isinstance(value, float):
            text = f"{value:10.3f}"
        else:
            text = f"{value:10d}"
        print(f"{name:28s}{text}")
    if args.json is not None:
        args.json.write_text(json.dumps({"meta": meta, "results": results}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
