"""Retained stock-observer trace; earlier buffered fixtures stay immutable."""

import json
import hashlib
from pathlib import Path

from simulatte import Trace, Provenance
from simulatte.trace import ChunkLimits
from prototypes.native_engine.model import Job
from .compiled import CompiledStream
from .live_observation import LiveTraceStream
from .trace_fixture import plain

ROOT = Path(__file__).resolve().parent


def generate(destination):
    destination.mkdir(parents=True, exist_ok=True)
    path, raw = destination / "observed.simtrace", destination / "observed.source"
    jobs = tuple(Job(i // 3, (i % 2, (i + 1) % 2), (1 + i % 3, 0.5 + i % 2), i % 5 + 3) for i in range(12))
    provenance = Provenance(
        model=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source=hashlib.sha256((ROOT / "csrc/dynamic_engine.c").read_bytes()).hexdigest(),
        inputs=hashlib.sha256(repr(jobs).encode()).hexdigest(),
        dependencies=hashlib.sha256((ROOT.parents[1] / "uv.lock").read_bytes()).hexdigest(),
    )
    stream = LiveTraceStream(
        CompiledStream,
        2,
        wip_cap=2,
        policy="spt",
        trace_path=path,
        raw_path=raw,
        warmup=2,
        chunk_limits=ChunkLimits(max_events=17, max_latency_s=float("inf")),
        debug=True,
        provenance=provenance,
    )
    try:
        for job in jobs:
            stream.advance(job.arrival)
            stream.add(job)
        stream.run()
        stream.result()
    finally:
        stream.close()
    trace = Trace.open(path)
    trace.check()
    assert trace.verify() is True
    chosen = {trace.cursor_range[0], trace.cursor_range[1]}
    for kind in (
        "entity.created",
        "psp.entered",
        "shopfloor.entered",
        "job.queued",
        "job.granted",
        "operation.started",
        "operation.completed",
        "shopfloor.wip_updated",
        "job.released",
        "job.finished",
        "policy.decision",
        "entity.retired",
    ):
        event = next(e for e in trace.events() if e.type == kind)
        chosen.add((event.t, event.seq))
        chosen.add((event.t, event.seq - 1))
    checks = []
    for cursor in sorted(chosen):
        kpis = {}
        for key, points in trace.kpi_series().items():
            eligible = [point for point in points if point[0] <= cursor]
            kpis[key] = eligible[-1][1] if eligible else None
        checks.append(dict(cursor=cursor, state=trace.state_at(cursor), kpis=kpis))
    expected = dict(
        cursor_range=trace.cursor_range,
        outcome=trace.outcome,
        scalars=trace.kpis(),
        checks=checks,
        events=[(e.t, e.seq, e.type) for e in trace.inspect_events()],
        digest=trace.fingerprint.digest,
    )
    (destination / "observed.expected.json").write_text(json.dumps(plain(expected), indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            dict(
                digest=trace.fingerprint.digest,
                domain=sum(1 for _ in trace.events()),
                observers=sum(1 for _ in trace.observer_events()),
                chunks=len(trace.index),
            )
        )
    )


if __name__ == "__main__":
    generate(ROOT / "fixtures")
