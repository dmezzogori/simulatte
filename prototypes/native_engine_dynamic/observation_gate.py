"""Stock collector and event-time read differential gate, including horizons."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
import platform
import tempfile

from simulatte import Trace
from simulatte.psp import PspEntered, PspExited
from simulatte.server import JobQueued, JobGranted, JobReleased, ServerWorkCredited
from simulatte.shopfloor import ShopFloorEntered, ShopFloorWipUpdated, OperationStarted, OperationCompleted, JobFinished
from .compiled import CompiledStream
from .feedback import feed
from .live_observation import LiveTraceStream, ProductionLiveStream, values
from .production_benchmark import source_hash, sources
from .scalar_benchmark import digest, libraries
from .semantics_gate import workloads
from .simultaneous import quantized
from .validation import fingerprint

EVENTS = (
    PspEntered,
    PspExited,
    ShopFloorEntered,
    JobQueued,
    JobGranted,
    ServerWorkCredited,
    OperationStarted,
    OperationCompleted,
    ShopFloorWipUpdated,
    JobReleased,
    JobFinished,
)


def equivalent(a, b):
    if isinstance(a, dict):
        assert a.keys() == b.keys()
        for key in a:
            equivalent(a[key], b[key])
    elif isinstance(a, (tuple, list)):
        assert len(a) == len(b)
        for x, y in zip(a, b):
            equivalent(x, y)
    elif isinstance(a, (int, float)) and not isinstance(a, bool):
        assert math.isclose(a, b, rel_tol=2e-11, abs_tol=1e-8), (a, b)
    else:
        assert a == b, (a, b)


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def probe(stream, env):
    floor = env.entities.get("shopfloor")
    servers = floor.servers
    captured = []

    def get_time(fn):
        try:
            return fn()
        except ValueError:
            return "unavailable"

    def observe(event):
        job = env.entities.get(event.job) if hasattr(event, "job") else None
        captured.append(
            dict(
                type=event.type_name,
                t=event.t,
                now=stream.now,
                floor=(len(floor.jobs), len(floor.jobs_done)),
                wip={server.id: value for server, value in floor.wip.items()},
                servers=[(server.count, len(server.queue), server.worked_time) for server in servers],
                job=None
                if job is None
                else (
                    job.id,
                    job.created_at,
                    job.psp_exit_at,
                    job.finished_at,
                    job.done,
                    job.processing_times,
                    get_time(lambda: job.time_in_psp),
                    get_time(lambda: job.time_in_shopfloor),
                ),
            )
        )

    env.bus.subscribe(observe, EVENTS)
    return captured


class Checkpoints:
    def __init__(self, stream):
        self.stream, self.snapshots = stream, []

    def advance(self, until):
        self.stream.advance(until)
        self.snapshots.append(values(self.stream))

    def add(self, job):
        handle = self.stream.add(job)
        self.snapshots.append(values(self.stream))
        return handle

    def inspect(self, handle):
        return self.stream.inspect(handle)


def case(
    templates,
    directory,
    *,
    cap,
    policy,
    producer,
    level,
    warmup,
    horizon=None,
    delivery="boundary",
    interest_checks=False,
):
    results = []
    for backend in ("production", "native"):
        path = directory / f"{backend}.simtrace" if level != "none" else None
        stream = (
            ProductionLiveStream(
                templates.servers, wip_cap=cap, policy=policy, mode=level, trace_path=path, warmup=warmup
            )
            if backend == "production"
            else LiveTraceStream(
                CompiledStream,
                templates.servers,
                wip_cap=cap,
                policy=policy,
                level=level,
                trace_path=path,
                raw_path=directory / "source",
                warmup=warmup,
                delivery=delivery,
                debug=True,
                interest_checks=interest_checks,
            )
        )
        env = stream.env if backend == "production" else stream.projection.env
        events = probe(stream, env)
        checkpoints = Checkpoints(stream)
        try:
            # A partial run admits only jobs before its requested boundary.
            from prototypes.native_engine.model import Workload

            selected = (
                templates
                if horizon is None
                else Workload(templates.servers, tuple(j for j in templates.jobs if j.arrival < horizon))
            )
            jobs, decisions = feed(checkpoints, selected, producer)
            if horizon is None:
                stream.run()
                result = stream.result()
                trajectory = fingerprint(result)
            else:
                checkpoints.advance(horizon)
                trajectory = None
            final = values(stream)
        finally:
            stream.close()
        equivalent(final, values(stream))
        if level == "none":
            recorder = stream.recorder if backend == "production" else stream.projection.recorder
            assert recorder is None and env._digest is None and not env._projection_active
            assert not env._projection_listeners
            stored, series = final["scalars"], {}
        else:
            trace = Trace.open(path)
            trace.check()
            assert trace.verify() == (True if level == "full" else "not_verifiable")
            assert trace.outcome == "completed"
            equivalent(final["scalars"], trace.kpis())
            stored, series = trace.kpis(), trace.kpi_series()
        results.append(
            dict(
                trajectory=trajectory,
                jobs=jobs,
                decisions=decisions,
                final=final,
                checkpoints=checkpoints.snapshots,
                probes=events,
                stored=stored,
                series=series,
            )
        )
    a, b = results
    assert a["trajectory"] == b["trajectory"] and a["jobs"] == b["jobs"] and a["decisions"] == b["decisions"]
    equivalent(a["final"], b["final"])
    equivalent(a["probes"], b["probes"])
    if delivery == "boundary":
        equivalent(a["checkpoints"], b["checkpoints"])
    return dict(
        trajectory=a["trajectory"],
        final_sha256=[digest(canonical(r["final"])) for r in results],
        probe_sha256=[digest(canonical(r["probes"])) for r in results],
        checkpoints=len(a["checkpoints"]),
        events=len(a["probes"]),
        scalars=a["stored"],
    )


def gate(output):
    output.mkdir(parents=True, exist_ok=False)
    frozen, binaries = source_hash(), libraries()
    metadata = dict(
        source_sha256=frozen, files=sources(), libraries=binaries, interpreter=platform.python_implementation()
    )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    count = 0
    with tempfile.TemporaryDirectory(prefix="native-observers-") as tmp, (output / "rows.jsonl").open("w") as target:
        directory = Path(tmp)
        # Dense old counterexamples plus new fractional/burst workloads.
        templates = {**workloads(), **{f"wide-{i}": quantized(i, jobs=24) for i in range(8)}}
        for (name, workload), cap, policy, producer, level, warmup, horizon in itertools.product(
            templates.items(),
            (1, 2, 12),
            ("fifo", "spt"),
            ("independent", "feedback"),
            ("kpi", "full"),
            (0, 2),
            (None, 4),
        ):
            row = case(
                workload,
                directory,
                cap=cap,
                policy=policy,
                producer=producer,
                level=level,
                warmup=warmup,
                horizon=horizon,
            )
            target.write(
                json.dumps(
                    dict(
                        case=name,
                        cap=cap,
                        policy=policy,
                        producer=producer,
                        level=level,
                        warmup=warmup,
                        horizon=horizon,
                        **row,
                    )
                )
                + "\n"
            )
            count += 1
    assert source_hash() == frozen and libraries() == binaries
    from prototypes.native_engine.benchmark import file_hash

    summary = dict(
        source_sha256=frozen,
        configurations=count,
        model_runs=count * 2,
        collector_value_mismatches=0,
        event_time_probe_mismatches=0,
        boundary_checkpoint_mismatches=0,
        evidence={name: file_hash(output / name) for name in ("metadata.json", "rows.jsonl")},
    )
    (output / "verification.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gate(args.output)


if __name__ == "__main__":
    main()
