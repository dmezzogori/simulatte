"""One cold local-worker measurement per fresh interpreter (CPython or PyPy).

Run from an installed environment, or with PYTHONPATH=src for a source checkout.
The output separates coordinator setup, standalone snapshot, inspection,
submit-to-ready, ready-to-publication and close. Polling resolution is 50 ms;
short phases may be indistinguishable. RSS is OS high-water evidence, not a
browser/cache estimate. This harness does not establish saturation/cancel gates.
"""

import argparse
import json
from pathlib import Path
import platform
import resource
import sys
import tempfile
import time

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.snapshot import capture_source, discover_project

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.events <= 1000000:
        parser.error("--events must be in [1, 1000000]")
    timings = {}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="simulatte-execution-bench-") as directory:
        root = Path(directory)
        (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["plant.py"]\n')
        (root / "plant.py").write_text(MODEL.replace("EVENTS", str(args.events)))
        project = discover_project(root / "plant.py", root)
        mark = time.perf_counter()
        snapshot = capture_source(project)
        timings["standalone_snapshot_seconds"] = time.perf_counter() - mark
        mark = time.perf_counter()
        coordinator = Coordinator(project)
        timings["coordinator_setup_seconds"] = time.perf_counter() - mark
        try:
            mark = time.perf_counter()
            prepared = coordinator.prepare(timeout=120)
            timings["inspection_including_recapture_seconds"] = time.perf_counter() - mark
            state = coordinator.inputs.bind(prepared)
            coordinator.events()
            mark = time.perf_counter()
            key = coordinator.submit(state.request, state.revision, timeout=180)
            ready = None
            heartbeat_times, progress = [], []
            prefixes = 0
            while True:
                for event in coordinator.events():
                    if event["key"] is None or event["key"]["attempt_id"] != key.attempt_id:
                        continue
                    now = time.perf_counter()
                    if event["type"] == "ready":
                        ready = now
                    elif event["type"] == "heartbeat":
                        heartbeat_times.append(now)
                        progress.append(event["body"])
                    elif event["type"] == "trace_available":
                        prefixes += 1
                try:
                    result = coordinator.wait(key, timeout=0.05)
                    break
                except TimeoutError:
                    if time.perf_counter() - mark > 190:
                        raise
            ended = time.perf_counter()
            timings["submit_to_publication_seconds"] = ended - mark
            timings["submit_to_ready_seconds"] = None if ready is None else ready - mark
            timings["ready_to_publication_seconds"] = None if ready is None else ended - ready
            report = {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "events": args.events,
                "source_bytes": sum(entry.size for entry in snapshot.entries),
                "trace_bytes": (result.path / "run.simtrace").stat().st_size,
                "committed_prefix_notifications": prefixes,
                "heartbeat_samples": len(progress),
                "maximum_observed_heartbeat_gap_seconds": max(
                    (b - a for a, b in zip(heartbeat_times, heartbeat_times[1:])), default=None
                ),
                "maximum_observed_spool_bytes": max((item["spool_bytes"] for item in progress), default=None),
                "maximum_observed_spool_files": max((item["spool_files"] for item in progress), default=None),
            }
        finally:
            mark = time.perf_counter()
            coordinator.close()
            timings["close_seconds"] = time.perf_counter() - mark
    timings["total_seconds"] = time.perf_counter() - started
    scale = 1 if sys.platform == "darwin" else 1024
    report.update(timings)
    report["coordinator_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    report["reaped_children_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * scale
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
