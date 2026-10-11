"""Generate SP3 Python/TypeScript parity data without changing released fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from generate import PROVENANCE, build_cell_ops, fixed_volatile_metadata, plain

from simulatte import Environment, Trace, TraceRecorder
from simulatte.kpi import Collector, KPI
from simulatte.trace import ChunkLimits


class Values(Collector):
    kpis = (KPI("level", unit="items", kind=("series",), interpolation="linear", missing="hold"),)


def generate(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "viewer.simtrace"
    with fixed_volatile_metadata():
        env = Environment(seed=31, provenance=PROVENANCE)
        recorder = TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=3, max_latency_s=float("inf")))
        build_cell_ops(env)
        values = Values(env.entities.get("first")).attach(env)
        env.info("Preparation </script> & <img src=x>")
        env.activate()
        values.sample("level", 0)
        env.run(until=5)
        values.sample("level", 2)
        values.sample("level", 3)
        env.info("Same-time observer")
        env.run(until=10)
        values.sample("level", float("nan"))
        env.run(until=15)
        values.sample("level", 9)
        env.run()
        env.info("Last observer, after retirement")
        recorder.close()
    trace = Trace.open(path)
    trace.check()
    assert trace.verify()
    events = list(trace.inspect_events())
    bounds = trace.cursor_range
    assert bounds is not None
    document = {
        "events": [[e.t, e.seq, e.type] for e in events],
        "prelude": [[e.t, e.seq, e.type] for e in trace.observer_events(prelude=True)],
        "display": plain(trace.kpi_series_display),
        "samples": plain(trace.kpi_series()),
        "states": [
            {"cursor": [e.t, e.seq], "state": plain(trace.state_at(min((e.t, e.seq), bounds[1])))} for e in events
        ],
    }
    (destination / "viewer.expected.json").write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    generate(Path(__file__).parent / "viewer")
