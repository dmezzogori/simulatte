"""Replication throughput of the local execution coordinator, one configuration per process.

Run from an installed environment, or with PYTHONPATH=src for a source checkout.
The coordinator prepares a generated project (a `@model` emitting EVENTS domain
events) and runs it as an experiment of N replications, one distinct seed each, on
`--slots` local experiment slots of the SP5 scheduler (`SlotConfig(local=N)`). A
`Runner` baseline then runs the same model, horizon and seeds in-process, so the
per-replication difference is the cost of isolation, tracing and publication, not
of the simulation.

Phase times come from benchmark-only wrappers around `Coordinator._emit` and
`publish` (no source change), so they are exact to the thread that reports
them, not rounded to a polling interval. A replication starts when the scheduler
dispatches it (`queued`); `store_commit` is the time inside `publish`. Results are
single observations on a host that was not quiesced: they are not warmed-throughput
claims.

Replications that do not publish are reported in `failures` and the exit status is
1. `ready_to_publication` ends when `publish` returns. Throughput is measured from
`start_experiment` to `wait_experiment` returning; `prepare()` runs before and is
reported separately.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import sys
import tempfile
import time
from typing import Any

from simulatte.execution import coordinator as coordinator_module
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.snapshot import discover_project

MODEL = """from dataclasses import dataclass
from typing import ClassVar
from simulatte import model
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import DomainEvent, Deltas, event_type
class Counter(Entity, kind="benchmark_counter"):
    state_schema: ClassVar[StateSchema] = StateSchema({"value": FieldSpec("int")})
    def __init__(self, env):
        self.value = 0
        env.entities.attach(self, name="counter")
@event_type("benchmark.changed", touches={"benchmark_counter": ("value",)})
class Changed(DomainEvent):
    value: int
@dataclass(frozen=True)
class Params:
    iterations: int = EVENTS
@model(params=Params, horizon=EVENTS + 1)
def build(env, layout, params):
    counter = Counter(env)
    def work():
        for index in range(params.iterations):
            yield env.timeout(1)
            counter.value = index + 1
            if env.wants(Changed):
                env.emit(Changed(value=counter.value,
                    deltas=Deltas.build().set(counter.id, "value", counter.value).done()))
    env.process(work())
"""


def summary(values):
    return {
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "max": max(values),
        "min": min(values),
    }


def cpu_seconds(usage):
    return usage.ru_utime + usage.ru_stime


def run_coordinator(root, args):
    """One experiment of args.replications distinct seeds on args.slots slots; returns the measurements."""
    from simulatte.execution.slots import SlotConfig
    from simulatte.experiments import Experiment, Grid, Replications

    project = discover_project(root / "plant.py", root)
    marks = {}  # attempt_id -> {"queued"/"ready": perf_counter when the coordinator reported it}
    publish_seconds = {}  # attempt_id -> seconds inside publish
    publish_ended = {}  # attempt_id -> perf_counter when publish returned
    original_emit = Coordinator._emit
    original_publish = coordinator_module.publish

    def emit(self, kind, key, body):
        original_emit(self, kind, key, body)
        if key is not None and kind in ("queued", "ready"):
            marks.setdefault(key.attempt_id, {})[kind] = time.perf_counter()

    def timed_publish(store, key, *rest, **named):
        start = time.perf_counter()
        try:
            return original_publish(store, key, *rest, **named)
        finally:
            publish_ended[key.attempt_id] = time.perf_counter()
            publish_seconds[key.attempt_id] = publish_ended[key.attempt_id] - start

    Coordinator._emit = emit
    setattr(coordinator_module, "publish", timed_publish)
    coordinator = Coordinator(project, slots=SlotConfig(local=args.slots))
    try:
        mark = time.perf_counter()
        coordinator.inputs.bind(coordinator.prepare(timeout=120))
        prepare_seconds = time.perf_counter() - mark
        experiment = Experiment(
            name=f"throughput-{args.events}",
            model="plant.py:build",
            replications=Replications(args.replications),
            configurations=Grid({}),
            level="full",
        )
        cpu_start = cpu_seconds(resource.getrusage(resource.RUSAGE_SELF))
        loop_start = time.perf_counter()
        run_id = coordinator.start_experiment(experiment, timeout=180)
        report = coordinator.wait_experiment(run_id, timeout=190 * args.replications)
        loop_seconds = time.perf_counter() - loop_start
        loop_cpu = cpu_seconds(resource.getrusage(resource.RUSAGE_SELF)) - cpu_start
        samples = []
        failures = []
        for item in coordinator.store.replications(run_id):
            attempt = item["attempt"]
            if item["status"] != "succeeded":
                # Keep what completed: a failing replication is reported, not hidden.
                details = (
                    None if attempt is None else coordinator.store.attempt(_key(coordinator.store, attempt))["details"]
                )
                failures.append({"seed": item["seed"], "status": item["status"], "details": details})
                continue
            seen = marks[attempt]
            samples.append(
                {
                    "seed": item["seed"],
                    "slot": coordinator.store.attempt(_key(coordinator.store, attempt))["slot"],
                    "dispatched": seen["queued"] - loop_start,
                    "wall": publish_ended[attempt] - seen["queued"],
                    "submit_to_ready": seen["ready"] - seen["queued"],
                    "ready_to_publication": publish_ended[attempt] - seen["ready"],
                    "store_commit": publish_seconds[attempt],
                }
            )
        samples.sort(key=lambda sample: sample["dispatched"])
    finally:
        mark = time.perf_counter()
        coordinator.close()
        close_seconds = time.perf_counter() - mark
        Coordinator._emit = original_emit
        setattr(coordinator_module, "publish", original_publish)
    return (
        samples,
        failures,
        {
            "prepare_seconds": prepare_seconds,
            "loop_seconds": loop_seconds,
            "coordinator_loop_cpu_seconds": loop_cpu,
            "close_seconds": close_seconds,
            "run_status": report["status"],
        },
    )


def _key(store, attempt_id):
    from simulatte.execution.schema import AttemptKey

    with store._transaction() as db:
        row = db.execute("SELECT request_id,generation FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    return AttemptKey(row[0], attempt_id, row[1])


def run_baseline(args):
    """Same model, horizon and seeds through Runner machinery, sequentially, in this process."""
    from functools import partial

    from simulatte import Runner

    namespace: dict[str, Any] = {"__name__": "benchmark_model"}
    exec(compile(MODEL.replace("EVENTS", str(args.events)), "<benchmark-model>", "exec"), namespace)
    builder = partial(namespace["build"], layout=None, params=namespace["Params"]())
    walls = []
    for seed in range(args.replications):
        start = time.perf_counter()
        Runner(
            builder=builder,
            seeds=[seed],
            parallel=False,
            progress=False,
            extract_fn=lambda system: 0,
        ).run(until=args.events + 1)
        walls.append(time.perf_counter() - start)
    return walls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replications", type=int, default=32)
    parser.add_argument("--events", type=int, choices=(100, 20000), default=100)
    parser.add_argument("--slots", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument("--worker", default="local", help="label of the host or worker, recorded in the JSON")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if args.replications < 1:
        parser.error("--replications must be positive")
    with tempfile.TemporaryDirectory(prefix="simulatte-throughput-bench-") as directory:
        root = Path(directory)
        (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["plant.py"]\n')
        (root / "plant.py").write_text(MODEL.replace("EVENTS", str(args.events)))
        samples, failures, phases = run_coordinator(root, args)
    scale = 1 if sys.platform == "darwin" else 1024
    coordinator_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    baseline = run_baseline(args)
    if not samples:
        sys.exit(f"no replication completed: {failures}")
    count = len(samples)
    walls = [item["wall"] for item in samples]
    runner_mean = statistics.fmean(baseline)
    report = {
        "worker": args.worker,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "slots": args.slots,
        "events": args.events,
        "replications": args.replications,
        "completed_replications": count,
        "failures": failures,
        "replications_per_minute": 60 * count / phases["loop_seconds"],
        # Mean dispatch-to-publication time of one replication; slots overlap these.
        "coordinator_wall_per_replication": statistics.fmean(walls),
        "runner_wall_per_replication": runner_mean,
        "runner_wall_per_replication_median": statistics.median(baseline),
        "runner_replications_per_minute": 60 / runner_mean,
        "overhead_per_replication": statistics.fmean(walls) - runner_mean,
        "submit_to_ready": summary([item["submit_to_ready"] for item in samples]),
        "ready_to_publication": summary([item["ready_to_publication"] for item in samples]),
        "store_commit": summary([item["store_commit"] for item in samples]),
        "coordinator_cpu_per_replication": phases["coordinator_loop_cpu_seconds"] / count,
        "children_cpu_seconds_total": cpu_seconds(children),
        # Reaped children are only visible after close, so this includes the inspection run of prepare():
        # divide by replications + 1 executions.
        "children_cpu_per_execution": cpu_seconds(children) / (count + 1),
        "coordinator_peak_rss_bytes": coordinator_rss,
        "reaped_children_peak_rss_bytes": children.ru_maxrss * scale,
        "cpu_count": os.cpu_count(),
        "load_average": os.getloadavg(),
        "phases": phases,
        "runner_samples": baseline,
        "samples": samples,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.write_text(encoded)
    else:
        print(encoded, end="")
    if failures:
        sys.exit(f"{len(failures)} replications did not publish; {count} completed")


if __name__ == "__main__":
    main()
