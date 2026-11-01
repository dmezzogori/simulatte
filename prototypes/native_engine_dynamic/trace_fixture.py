"""Create the retained native fixture used by Python, TypeScript and browser checks."""

import hashlib
import json
from pathlib import Path

from simulatte import Trace, Provenance
from simulatte.trace import ChunkLimits
from prototypes.native_engine.model import Job, Workload

from .compiled import CompiledStream
from .studio_trace import TraceStream, validate_trace

ROOT = Path(__file__).resolve().parent


def plain(value):
    if isinstance(value, dict) or hasattr(value, "items"):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def generate(destination):
    destination.mkdir(parents=True, exist_ok=True)
    jobs = tuple(Job(i // 3, (i % 2, (i + 1) % 2), (1 + i % 3, 0.5 + i % 2), i % 5 + 3) for i in range(12))
    data = Workload(2, jobs)
    hashed = hashlib.sha256(repr(jobs).encode()).hexdigest()
    provenance = Provenance(
        model=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source=hashlib.sha256((ROOT / "csrc/dynamic_engine.c").read_bytes()).hexdigest(),
        inputs=hashed,
        dependencies=hashlib.sha256((ROOT.parents[1] / "uv.lock").read_bytes()).hexdigest(),
    )
    path, raw = destination / "native.simtrace", destination / "native.source"
    stream = TraceStream(
        CompiledStream,
        2,
        raw_path=raw,
        trace_path=path,
        wip_cap=2,
        policy="spt",
        provenance=provenance,
        parameters={"projection": "native-studio-research", "cap": 2},
        chunk_limits=ChunkLimits(max_events=17, max_latency_s=float("inf")),
        debug=True,
    )
    try:
        for job in jobs:
            stream.advance(job.arrival)
            stream.add(job)
        stream.run()
        result = stream.result()
    finally:
        stream.close()
    signature = validate_trace(path, data, result, wip_cap=2)
    trace = Trace.open(path)
    events = list(trace.events())
    chosen = {trace.cursor_range[0], trace.cursor_range[1]}
    for kind in (
        "entity.created",
        "psp.entered",
        "shopfloor.entered",
        "job.queued",
        "job.granted",
        "operation.started",
        "operation.completed",
        "job.released",
        "job.finished",
    ):
        event = next(e for e in events if e.type == kind)
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
        signature=signature,
        outcome=trace.outcome,
        cursor_range=trace.cursor_range,
        events=[(e.t, e.seq, e.type) for e in trace.inspect_events()],
        checks=checks,
        scalars=trace.kpis(),
        source_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
    )
    (destination / "native.expected.json").write_text(json.dumps(plain(expected), indent=2, sort_keys=True) + "\n")
    print(json.dumps(signature, indent=2))


if __name__ == "__main__":
    generate(ROOT / "fixtures")
