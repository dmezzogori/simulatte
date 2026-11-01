"""Characterize unchanged native semantics against actual production actors."""

from __future__ import annotations

import argparse
from collections import Counter
import itertools
import json
import math
from pathlib import Path
import platform
import random
import tempfile

from simulatte import Trace
from simulatte.collectors import (
    CurrentWorkloadCollector,
    EMACollector,
    ServerTimeSeries,
    ShopFloorKPIs,
    ShopFloorTimeSeries,
)
from simulatte.shopfloor import JobFinished
from prototypes.native_engine.model import Job, Workload

from .compiled import CompiledStream
from .direct_trace import DirectTraceStream
from .feedback import feed, validate_feed
from .production import ProductionStream
from .production_benchmark import source_hash, validate_output
from .rich_recording import validate_records
from .scalar_benchmark import digest, libraries
from .studio_trace import TraceStream
from .trace_benchmark import normalized_wire
from .validation import fingerprint, validate_result

BACKENDS = ("production", "native", "direct")
FIELDS = ("starts", "ends", "released", "finished", "dispatch")


def workloads():
    cases = {
        "empty": Workload(1, ()),
        "burst": Workload(1, tuple(Job(0, (0,), (d,), 10) for d in (2, 1, 1, 3))),
        "boundary": Workload(
            1,
            (Job(0, (0,), (2,), 10), Job(0, (0,), (1,), 10), Job(2, (0,), (1,), 5), Job(3, (0,), (1,), 5)),
        ),
        "cross": Workload(
            2,
            (Job(0, (0, 1), (1, 1), 10), Job(0, (1, 0), (1, 1), 10), Job(1, (0,), (1,), 10), Job(1, (1,), (1,), 10)),
        ),
        "chain": Workload(
            2, tuple(Job(float(i // 3), (i % 2, 1 - i % 2), (1, 1), float(8 - i % 3)) for i in range(12))
        ),
        # Deletion-minimized from quantized seed 1, cap 2, SPT, independent.
        "reduced": Workload(
            3,
            (
                Job(5, (1, 0), (1, 2), 10),
                Job(6, (0, 2, 1), (1, 2, 1), 10),
                Job(6, (0, 1), (1, 1), 10),
                Job(7, (1,), (1,), 10),
            ),
        ),
    }
    for seed in (0, 1):
        rng, at, jobs = random.Random(seed), 0, []
        for _ in range(24):
            at += rng.choice((0, 0, 1))
            route = tuple(rng.sample(range(3), rng.randint(1, 3)))
            jobs.append(Job(at, route, tuple(rng.choice((1, 1, 2)) for _ in route), rng.choice((10, 10, 20))))
        cases[f"quantized-{seed}"] = Workload(3, tuple(jobs))
    return cases


def stream_for(backend, workload, directory, *, cap=2, policy="spt", mode="full"):
    path, raw = directory / "out.simtrace", directory / "out.source"
    if backend == "production":
        return ProductionStream(workload.servers, wip_cap=cap, policy=policy, mode=mode, trace_path=path)
    if mode == "none":
        return CompiledStream(workload.servers, wip_cap=cap, policy=policy)
    cls = DirectTraceStream if backend == "direct" else TraceStream
    return cls(CompiledStream, workload.servers, wip_cap=cap, policy=policy, level=mode, raw_path=raw, trace_path=path)


def publisher(stream, backend):
    return stream.env if backend == "production" else stream.projection.env


def run_case(backend, templates, directory, *, cap, policy, producer, mode):
    stream = stream_for(backend, templates, directory, cap=cap, policy=policy, mode=mode)
    try:
        jobs, decisions = feed(stream, templates, producer)
        stream.run()
        result = stream.result()
    finally:
        stream.close()
    workload = Workload(templates.servers, jobs)
    validate_result(workload, result, wip_cap=cap)
    if producer == "feedback":
        validate_feed(templates, jobs, decisions, result)
    row = dict(
        trajectory=fingerprint(result),
        values={key: result[key] for key in FIELDS},
        generated=digest([(j.arrival, j.route, j.durations, j.due) for j in jobs]),
        # Production's initial SimPy clock is int 0, native's is float 0.0.
        # Those numerically equal observation values are the same contract.
        decisions=digest(
            [
                (
                    probe,
                    *(None if value is None else float(value) for value in (arrival, released, finished)),
                    state,
                    choice,
                )
                for probe, arrival, released, finished, state, choice in decisions
            ]
        ),
        kpis=result["kpis"],
        series=None,
        series_peaks=None,
        wire=None,
        signature=None,
        source=None,
    )
    if mode != "none":
        path = directory / "out.simtrace"
        row["signature"] = validate_output(path, jobs, result, mode=mode, backend=backend)
        row["series"] = row["signature"]["series_values_sha256"]
        row["series_peaks"] = {
            key: max(value for _, value in points) for key, points in Trace.open(path).kpi_series().items()
        }
        row["wire"] = normalized_wire(path)[0]
        if backend != "production":
            raw = directory / "out.source"
            validate_records(raw, workload, result, cap=cap, policy=policy)
            row["source"] = digest(raw.read_bytes().hex())
    return row


def differences(a, b):
    changed = [key for key in FIELDS if a["values"][key] != b["values"][key]]
    changed.extend(key for key in ("generated", "decisions", "series") if a[key] != b[key])
    if a["kpis"] is not None:
        for key in a["kpis"]:
            av, bv = a["kpis"][key], b["kpis"][key]
            pairs = zip(av, bv) if isinstance(av, list) else [(av, bv)]
            if not all(math.isclose(x, y, rel_tol=2e-11, abs_tol=1e-8) for x, y in pairs):
                changed.append(f"kpi:{key}")
    return changed


def boundary(backend, directory):
    workload = Workload(1, (Job(0, (0,), (2,), 10), Job(2, (0,), (1,), 10)))
    stream = stream_for(backend, workload, directory, cap=1)
    env = publisher(stream, backend)
    delivered = []
    env.bus.subscribe(lambda event: delivered.append((event.t, event.job)), (JobFinished,))
    try:
        first = stream.add(workload.jobs[0])
        initial = stream.inspect(first)["state"]
        stream.advance(2)
        at = stream.inspect(first)["state"]
        stream.advance(2)
        repeated = stream.inspect(first)["state"]
        second = stream.add(workload.jobs[1])
        added = stream.inspect(second)["state"]
        stream.advance(math.nextafter(2, math.inf))
        after = [stream.inspect(h)["state"] for h in (first, second)]
        before_drain = list(delivered)
        publisher_time = env.now
        stream.run()
        result = stream.result()
    finally:
        stream.close()
    assert (initial, at, repeated, added, after) == (
        "scheduled",
        "active",
        "active",
        "scheduled",
        ["finished", "active"],
    )
    assert delivered == [(2.0, "job-0"), (3.0, "job-1")]
    return dict(
        before_drain=before_drain, publisher_time=publisher_time, final=delivered, trajectory=fingerprint(result)
    )


def observers(directory):
    output = []
    templates = workloads()["boundary"]
    classes = (EMACollector, ShopFloorTimeSeries, CurrentWorkloadCollector, ServerTimeSeries, ShopFloorKPIs)
    for backend, cls in itertools.product(BACKENDS, classes):
        stream = stream_for(backend, templates, directory)
        env = publisher(stream, backend)
        row = dict(backend=backend, collector=cls.__name__, status="ok", phase="construct")
        if cls is ShopFloorKPIs:
            # Benchmark totals and stock mean KPIs both declare "makespan".
            # Isolate the stock capability probe from that unrelated name clash.
            custom = stream.kpis if backend == "production" else stream.projection.kpis
            env._collectors.remove(custom)
        try:
            scope = env.entities.get("server-0" if cls is ServerTimeSeries else "shopfloor")
            collector = cls(scope)
            row["phase"] = "attach"
            collector.attach(env)
            row["phase"] = "feed/run"
            feed(stream, templates, "independent")
            stream.run()
            stream.result()
            row["phase"] = "close"
        except (AttributeError, RuntimeError) as exc:
            row.update(status="unsupported", error=type(exc).__name__, message=str(exc))
        finally:
            # A collector that failed attach may still be registered. Remove it
            # before close so cleanup cannot repeat its failure or hide evidence.
            if row["status"] == "unsupported":
                for attached in tuple(env.collectors):
                    if isinstance(attached, cls):
                        if attached._subscription is not None:
                            attached._subscription.cancel()
                        env._collectors.remove(attached)
            stream.close()
        output.append(row)
    return output


def event_observer(backend, directory):
    templates = workloads()["boundary"]
    rows, received = [], []
    for observe in (False, True):
        stream = stream_for(backend, templates, directory)
        if observe:
            publisher(stream, backend).bus.subscribe(
                lambda event: received.append(
                    (event.t, event.job, event.makespan, event.lateness, event.total_queue_time)
                ),
                (JobFinished,),
            )
        try:
            jobs, decisions = feed(stream, templates, "independent")
            stream.run()
            result = stream.result()
        finally:
            stream.close()
        trace = Trace.open(directory / "out.simtrace")
        trace.check()
        assert trace.verify() is True
        rows.append((fingerprint(result), jobs, decisions, normalized_wire(directory / "out.simtrace"), trace.kpis()))
    assert rows[0] == rows[1], "event observer changed result or trace"
    assert len(received) == len(templates.jobs)
    return received


def gate():
    frozen, binaries = source_hash(), libraries()
    rows = []
    with tempfile.TemporaryDirectory(prefix="native-semantics-") as tmp:
        directory = Path(tmp)
        for (name, templates), cap, policy, producer, mode in itertools.product(
            workloads().items(), (1, 2, 12), ("fifo", "spt"), ("independent", "feedback"), ("none", "kpi", "full")
        ):
            results = {
                backend: run_case(backend, templates, directory, cap=cap, policy=policy, producer=producer, mode=mode)
                for backend in BACKENDS
            }
            assert results["native"] == results["direct"], "aligned/direct mismatch"
            changed = differences(results["production"], results["direct"])
            rows.append(
                dict(
                    case=name,
                    cap=cap,
                    policy=policy,
                    producer=producer,
                    mode=mode,
                    differences=changed,
                    results=results,
                )
            )
        boundaries = {backend: boundary(backend, directory) for backend in BACKENDS}
        observed = {backend: event_observer(backend, directory) for backend in BACKENDS}
        assert observed["production"] == observed["native"] == observed["direct"]
        collectors = observers(directory)
        assert all(row["status"] == "ok" for row in collectors if row["backend"] == "production")
        witness = {}
        for backend in ("production", "direct"):
            run_case(
                backend, workloads()["reduced"], directory, cap=2, policy="spt", producer="independent", mode="full"
            )
            witness[backend] = [
                dict(t=event.t, seq=event.seq, type=event.type, payload=dict(event.payload))
                for event in Trace.open(directory / "out.simtrace").events()
                if event.t == 10
                and event.type
                in (
                    "operation.completed",
                    "job.released",
                    "job.finished",
                    "job.queued",
                    "job.granted",
                    "policy.decision",
                    "shopfloor.entered",
                    "operation.started",
                )
            ]
    assert source_hash() == frozen and libraries() == binaries
    return dict(
        source_sha256=frozen,
        libraries=binaries,
        interpreter=platform.python_implementation(),
        rows=rows,
        boundaries=boundaries,
        event_observers=observed,
        collectors=collectors,
        reduced_time_10_events=witness,
        summary=dict(
            comparisons=len(rows),
            changed=sum(bool(row["differences"]) for row in rows),
            changed_times=sum(bool(set(row["differences"]) & set(FIELDS[:-1])) for row in rows),
            difference_counts=dict(Counter(key for row in rows for key in row["differences"])),
            exported_floor_peak_mismatches={
                backend: sum(
                    row["results"][backend]["kpis"]["max_floor_wip"]
                    != row["results"][backend]["series_peaks"]["shopfloor/floor_wip"]
                    for row in rows
                    if row["mode"] != "none"
                )
                for backend in BACKENDS
            },
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = gate()
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output["summary"]))


if __name__ == "__main__":
    main()
