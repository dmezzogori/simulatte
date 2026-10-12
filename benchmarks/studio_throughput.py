"""Replication throughput of the local execution coordinator, one configuration per process.

Run from an installed environment, or with PYTHONPATH=src for a source checkout.
The coordinator runs N replications of a generated project (a `@model` emitting
EVENTS domain events), one distinct seed each, submitted and awaited one after
the other. A `Runner` baseline then runs the same model, horizon and seeds
in-process, so the per-replication difference is the cost of isolation, tracing
and publication, not of the simulation.

Phase times come from benchmark-only wrappers around `Coordinator._emit` and
`publish` (no source change), so they are exact to the thread that reports
them, not rounded to a polling interval. `store_commit` is the time inside
`publish`. Before the SP5 scheduler exists only `--slots 1` is measurable;
larger counts exit with a message. Results are single observations on a host
that was not quiesced: they are not warmed-throughput claims.

A replication that does not publish ends the sequence. The JSON then holds the
replications that completed plus a `failure` record, and the exit status is 1.
`ready_to_publication` ends when `publish` returns; `wait_return_lag` is how much
later (or, if `wait` won the race, earlier) the caller of `wait` resumed.
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
    """Sequential submit/wait of args.replications distinct seeds; returns the measurements."""
    project = discover_project(root / "plant.py", root)
    marks = {}  # attempt_id -> {"ready": perf_counter when the coordinator reported readiness}
    publish_seconds = {}  # attempt_id -> seconds inside publish
    publish_ended = {}  # attempt_id -> perf_counter when publish returned
    original_emit = Coordinator._emit
    original_publish = coordinator_module.publish

    def emit(self, kind, key, body):
        original_emit(self, kind, key, body)
        if key is not None and kind == "ready":
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
    coordinator = Coordinator(project)
    try:
        mark = time.perf_counter()
        prepared = coordinator.prepare(timeout=120)
        prepare_seconds = time.perf_counter() - mark
        loop_seconds = loop_cpu = 0.0
        samples = []
        rss_samples = []
        failure = None
        cpu_start = cpu_seconds(resource.getrusage(resource.RUSAGE_SELF))
        loop_start = time.perf_counter()
        for seed in range(args.replications):
            request = coordinator.make_request(prepared, seed=seed)
            begin = time.perf_counter()
            key = coordinator.submit(request, prepared.revision, timeout=180)
            try:
                coordinator.wait(key, timeout=190)
            except coordinator_module.ExecutionFailure as error:
                # Keep what completed: a failing replication ends the sequence, and is reported, not hidden.
                failure = {"seed": seed, "status": error.status, "details": error.details}
                break
            returned = time.perf_counter()
            coordinator.events()
            # wait() can return between the store commit and the coordinator thread's report of it.
            while key.attempt_id not in publish_ended:
                time.sleep(0.0005)
            seen = marks[key.attempt_id]
            samples.append(
                {
                    "seed": seed,
                    "wall": returned - begin,
                    "submit_to_ready": seen["ready"] - begin,
                    "ready_to_publication": publish_ended[key.attempt_id] - seen["ready"],
                    "store_commit": publish_seconds[key.attempt_id],
                    "wait_return_lag": returned - publish_ended[key.attempt_id],
                }
            )
            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss_samples.append(usage.ru_maxrss)
            loop_seconds = time.perf_counter() - loop_start
            loop_cpu = cpu_seconds(usage) - cpu_start
    finally:
        mark = time.perf_counter()
        coordinator.close()
        close_seconds = time.perf_counter() - mark
        Coordinator._emit = original_emit
        setattr(coordinator_module, "publish", original_publish)
    return (
        samples,
        failure,
        {
            "prepare_seconds": prepare_seconds,
            "loop_seconds": loop_seconds,
            "coordinator_loop_cpu_seconds": loop_cpu,
            "close_seconds": close_seconds,
            "coordinator_rss_after_each_bytes": rss_samples,
        },
    )


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
    if args.slots != 1:
        sys.exit(
            f"--slots {args.slots} requires the SP5 scheduler; "
            "only --slots 1 is measurable on the single-slot coordinator"
        )
    with tempfile.TemporaryDirectory(prefix="simulatte-throughput-bench-") as directory:
        root = Path(directory)
        (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["plant.py"]\n')
        (root / "plant.py").write_text(MODEL.replace("EVENTS", str(args.events)))
        samples, failure, phases = run_coordinator(root, args)
    scale = 1 if sys.platform == "darwin" else 1024
    coordinator_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    baseline = run_baseline(args)
    if not samples:
        sys.exit(f"no replication completed: {failure}")
    count = len(samples)
    walls = [item["wall"] for item in samples]
    warm_walls = walls[1:]
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
        "failure": failure,
        "replications_per_minute": 60 * count / phases["loop_seconds"],
        "replications_per_minute_excluding_first": (60 * len(warm_walls) / sum(warm_walls) if warm_walls else None),
        "coordinator_wall_per_replication": statistics.fmean(walls),
        "coordinator_wall_per_replication_excluding_first": statistics.fmean(warm_walls) if warm_walls else None,
        "runner_wall_per_replication": runner_mean,
        "runner_wall_per_replication_median": statistics.median(baseline),
        "runner_replications_per_minute": 60 / runner_mean,
        "overhead_per_replication": statistics.fmean(walls) - runner_mean,
        "overhead_per_replication_excluding_first": (
            statistics.fmean(warm_walls) - runner_mean if warm_walls else None
        ),
        "first_replication": samples[0],
        "submit_to_ready": summary([item["submit_to_ready"] for item in samples]),
        "ready_to_publication": summary([item["ready_to_publication"] for item in samples]),
        "store_commit": summary([item["store_commit"] for item in samples]),
        "wait_return_lag": summary([item["wait_return_lag"] for item in samples]),
        "coordinator_cpu_per_replication": phases["coordinator_loop_cpu_seconds"] / count,
        "children_cpu_seconds_total": cpu_seconds(children),
        # Reaped children are only visible after close, so this includes the inspection run of prepare():
        # divide by replications + 1 executions.
        "children_cpu_per_execution": cpu_seconds(children) / (count + 1),
        "coordinator_peak_rss_bytes": coordinator_rss,
        "reaped_children_peak_rss_bytes": children.ru_maxrss * scale,
        "cpu_count": os.cpu_count(),
        "phases": {key: value for key, value in phases.items() if key != "coordinator_rss_after_each_bytes"},
        "runner_samples": baseline,
        "samples": samples,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.write_text(encoded)
    else:
        print(encoded, end="")
    if failure is not None:
        sys.exit(f"replication with seed {failure['seed']} ended {failure['status']}; {count} completed")


if __name__ == "__main__":
    main()
