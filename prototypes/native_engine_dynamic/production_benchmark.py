"""Direct production baseline; profiling runs are separate from timing runs."""

from __future__ import annotations

import argparse
from datetime import datetime, UTC
import gc
import importlib.metadata
import itertools
import json
import math
import os
from pathlib import Path
import platform
import profile as cpu_profile
import random
import resource
import statistics
import subprocess
import sys
import time

from simulatte import Trace, Provenance, TraceRecorder
import simulatte.trace.writer as production_writer
from simulatte.trace.sink import FileSink
from simulatte.trace.format import RecordType
from prototypes.native_engine import benchmark as static
from prototypes.native_engine.model import Workload
from prototypes.native_engine.workloads import read

from .benchmark import REPO, ROOT, PHASES
from .compiled import CompiledStream, extension
from .feedback import feed, validate_feed
from .production import ProductionStream
from .rich_recording import validate_records
from .scalar_benchmark import digest, libraries
from .studio_trace import TraceStream, SCALARS
from .trace_benchmark import normalized_wire, sources as trace_sources
from .validation import fingerprint, validate_result


def sources():
    return {
        **trace_sources(),
        **{
            str(p.relative_to(REPO)): static.file_hash(p)
            for p in (
                REPO / "specs/research/native-engine-data/production_analysis.py",
                ROOT / "tests/test_production.py",
            )
        },
    }


def source_hash():
    return digest(sources())


def cases():
    return [
        dict(interpreter=i, producer=p, mode=m, backend=b, jobs=5000)
        for i, p, m, b in itertools.product(
            ("CPython", "PyPy"), ("independent", "feedback"), ("none", "kpi", "full"), ("production", "native")
        )
    ]


def case_id(row):
    return "-".join(str(row[k]) for k in ("interpreter", "producer", "jobs", "mode", "backend"))


def equivalent(rows):
    groups = {}
    for row in rows:
        group = groups.setdefault((row["producer"], row["jobs"], row["input_sha256"]), {})
        for key in ("trajectory_sha256", "generated_sha256", "decisions_sha256", "operations"):
            assert group.setdefault(key, row[key]) == row[key], key
        kpis = row["kpis"]
        if kpis is not None:
            first = group.setdefault("kpis", kpis)
            assert first.keys() == kpis.keys()
            for key in kpis:
                assert math.isclose(first[key], kpis[key], rel_tol=2e-11, abs_tol=1e-8), key
        # Series include global seq, which legitimately differs between event streams.
        if row["series_values_sha256"] is not None:
            assert group.setdefault("series", row["series_values_sha256"]) == row["series_values_sha256"], "WIP series"


def validate_output(path, jobs, result, *, mode, backend):
    trace = Trace.open(path)
    trace.check()
    assert trace.level == mode and trace.outcome == "completed" and not trace.truncated
    assert trace.verify() == (True if mode == "full" else "not_verifiable")
    scalars = trace.kpis()
    for name in SCALARS:
        expected = result[name] if name in ("completed", "makespan") else result["kpis"][name]
        assert scalars[f"shopfloor/{name}"] == expected, name
    series = {key: [(cursor[0], value) for cursor, value in values] for key, values in trace.kpi_series().items()}
    assert {k: len(v) for k, v in series.items()} == {
        "shopfloor/system_wip": 2 * len(jobs) + 1,
        "shopfloor/floor_wip": 2 * len(jobs) + 1,
    }
    assert all(v[0] == (0.0, 0.0) and v[-1][1] == 0 for v in series.values())
    domain, observers = 0, 0
    event_counts = {}
    if mode == "full":
        state = trace.state_at(trace.cursor_range[1])
        assert state["shopfloor"]["jobs_in_system"] == 0 and state["psp"]["jobs"] == ()
        for sid in range(len(result["kpis"]["busy"])):
            fields = state[f"server-{sid}"]
            assert fields["queue"] == fields["users"] == ()
            assert math.isclose(fields["worked_time"], result["kpis"]["busy"][sid], rel_tol=2e-11, abs_tol=1e-8)
        assert sum(k.startswith("job-") for k in state) == (len(jobs) if backend == "native" else 0)
        offsets, starts, ends, releases, finishes = [0], [], [], [], []
        for job in jobs:
            offsets.append(offsets[-1] + len(job.route))
        for event in trace.events():
            domain += 1
            event_counts[event.type] = event_counts.get(event.type, 0) + 1
            if event.type in ("operation.started", "operation.completed"):
                identity = int(event.payload["job"].split("-")[1])
                op = offsets[identity] + event.payload["op_index"]
                expected = result["starts"] if event.type == "operation.started" else result["ends"]
                assert event.t == expected[op]
                (starts if event.type == "operation.started" else ends).append(op)
            elif event.type in ("shopfloor.entered", "job.finished"):
                identity = int(event.payload["job"].split("-")[1])
                expected = result["released"] if event.type == "shopfloor.entered" else result["finished"]
                assert event.t == expected[identity]
                (releases if event.type == "shopfloor.entered" else finishes).append(identity)
        assert sorted(starts) == sorted(ends) == list(range(offsets[-1]))
        assert starts == result["dispatch"] and sorted(releases) == sorted(finishes) == list(range(len(jobs)))
        for event in trace.observer_events():
            observers += 1
            event_counts[event.type] = event_counts.get(event.type, 0) + 1
        prelude_observers = sum(1 for _ in trace.observer_events(prelude=True))
        assert event_counts.get("entity.retired", 0) == (len(jobs) if backend == "production" else 0)
    return dict(
        digest=trace.fingerprint.digest,
        kpis=scalars,
        series_values_sha256=digest(series),
        domain_events=domain,
        observer_events=observers,
        prelude_observer_events=prelude_observers if mode == "full" else 0,
        event_counts=event_counts,
        chunks=len(trace.index),
    )


def sample(templates, args, raw, path, provenance):
    gc.collect()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu_begin = usage.ru_utime + usage.ru_stime
    begin = time.perf_counter()
    if args.backend == "production":
        stream = ProductionStream(templates.servers, mode=args.mode, trace_path=path, provenance=provenance)
    elif args.mode == "none":
        stream = CompiledStream(templates.servers, wip_cap=12, policy="spt")
    else:
        stream = TraceStream(
            CompiledStream,
            templates.servers,
            raw_path=raw,
            trace_path=path,
            wip_cap=12,
            policy="spt",
            level=args.mode,
            provenance=provenance,
        )
    built = time.perf_counter()
    try:
        jobs, decisions = feed(stream, templates, args.producer)
        fed = time.perf_counter()
        stream.run()
        ran = time.perf_counter()
        result = stream.result()
        extracted = time.perf_counter()
    finally:
        stream.close()
    del stream
    gc.collect()
    closed = time.perf_counter()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    points = (begin, built, fed, ran, extracted, closed)
    timing = dict(zip(PHASES[:-1], (b - a for a, b in zip(points, points[1:]))))
    timing["total"] = closed - begin
    timing["process_cpu"] = usage.ru_utime + usage.ru_stime - cpu_begin
    return result, jobs, decisions, timing, usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)


def worker(args):
    assert Path(production_writer.__file__).resolve() == REPO / "src/simulatte/trace/writer.py", (
        "wrong production import"
    )
    frozen, binaries = source_hash(), libraries()
    original = read(args.input)
    templates = Workload(original.servers, original.jobs[: args.jobs])
    assert len(templates.jobs) == args.jobs
    extension()  # imports and DSO loading excluded for both paths
    provenance = Provenance(
        model="native-production-baseline-v1",
        source=frozen,
        inputs=static.file_hash(args.input),
        dependencies=static.file_hash(REPO / "uv.lock"),
    )
    (ROOT / ".runs").mkdir(exist_ok=True)
    raw, path = (ROOT / ".runs" / f"production-{os.getpid()}{suffix}" for suffix in (".source", ".simtrace"))
    warmups, samples, sizes, wires = [], [], [], []
    expected, signature = None, None
    count = 0 if args.probe else args.warmups
    try:
        for index in range(count + (1 if args.probe else args.repeats)):
            result, jobs, decisions, timing, rss = sample(templates, args, raw, path, provenance)
            current = dict(
                trajectory_sha256=fingerprint(result),
                generated_sha256=digest([(j.arrival, j.route, j.durations, j.due) for j in jobs]),
                decisions_sha256=digest(decisions),
                kpis=None,
                series_values_sha256=None,
            )
            if args.mode != "none":
                wire, chunks = normalized_wire(path)
                wires.append(wire)
                assert len(set(wires)) == 1, "encoded drift"
                trace = Trace.open(path)
                assert trace.manifest["complete"]
                current.update(kpis=trace.kpis(), stored_digest=trace.fingerprint.digest)
                series = {
                    key: [(cursor[0], value) for cursor, value in values] for key, values in trace.kpi_series().items()
                }
                current["series_values_sha256"] = digest(series)
                del trace
            if expected is None:
                validate_result(Workload(templates.servers, jobs), result, wip_cap=12)
                if args.producer == "feedback":
                    validate_feed(templates, jobs, decisions, result)
                if args.mode != "none":
                    signature = validate_output(path, jobs, result, mode=args.mode, backend=args.backend)
                    assert signature["chunks"] == chunks
                    if args.backend == "native":
                        validate_records(raw, Workload(templates.servers, jobs), result, cap=12, policy="spt")
                expected = current
            assert expected == current, "result or trace drift"
            (warmups if index < count else samples).append(timing)
            sizes.append(path.stat().st_size if args.mode != "none" else 0)
            operations = sum(len(j.route) for j in jobs)
            counters = {key: result[key] for key in ("completed", "events", "callbacks", "records", "makespan")}
            del result, jobs, decisions
        assert source_hash() == frozen and libraries() == binaries, "worker provenance changed"
        return dict(
            study="direct-production",
            interpreter=platform.python_implementation(),
            python=sys.version,
            backend=args.backend,
            producer=args.producer,
            mode=args.mode,
            jobs=args.jobs,
            operations=operations,
            source_sha256=frozen,
            libraries=binaries,
            input_sha256=static.file_hash(args.input),
            **expected,
            trace_signature=signature,
            trace_wire_sha256=wires[0] if wires else None,
            counters=counters,
            trace_bytes=sizes[-1],
            trace_sizes=sizes,
            source_bytes=raw.stat().st_size if raw.exists() else 0,
            versions={name: importlib.metadata.version(name) for name in ("simulatte", "simpy", "msgpack")},
            warmups=warmups,
            samples=samples,
            peak_rss_bytes=rss if args.probe else None,
        )
    finally:
        raw.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


def matrix(args):
    args.output.mkdir(parents=True, exist_ok=False)
    frozen, binaries = source_hash(), libraries()
    path = REPO / "prototypes/native_engine/.inputs/jobs-5000-load-0.95.json"
    order = cases()
    random.Random(20261104).shuffle(order)
    meta = dict(
        created=datetime.now(UTC).isoformat(),
        source_sha256=frozen,
        files=sources(),
        libraries=binaries,
        input=str(path.relative_to(REPO)),
        input_sha256=static.file_hash(path),
        seed=20261104,
        platform=platform.platform(),
        host=platform.node(),
        case_order=[case_id(c) for c in order],
        command=" ".join(sys.argv),
        starting_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        writer=dict(
            max_events=10000, max_bytes=1 << 20, max_pending_bytes=64 << 20, max_latency_s="infinity", fsync=False
        ),
    )
    (args.output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    rows = []
    for case in order:
        executable = sys.executable if case["interpreter"] == "CPython" else str(args.pypy.absolute())
        command = [
            executable,
            "-m",
            "prototypes.native_engine_dynamic.production_benchmark",
            "worker",
            "--input",
            str(path),
            "--warmups",
            "1" if case["interpreter"] == "CPython" else "7",
        ]
        for key in ("producer", "mode", "backend", "jobs"):
            command.extend([f"--{key}", str(case[key])])
        env = dict(os.environ, PYTHONHASHSEED="0")
        row = json.loads(subprocess.check_output(command, env=env, cwd=REPO, text=True))
        probe = json.loads(subprocess.check_output([*command, "--probe"], env=env, cwd=REPO, text=True))
        for r in (row, probe):
            assert all(r[k] == v for k, v in case.items())
            assert (
                r["source_sha256"] == frozen
                and r["libraries"] == binaries
                and r["input_sha256"] == meta["input_sha256"]
            )
        equivalent([row, probe])
        assert (
            row["trace_signature"] == probe["trace_signature"]
            and row["trace_wire_sha256"] == probe["trace_wire_sha256"]
        )
        assert source_hash() == frozen and libraries() == binaries
        row.update(case=case_id(case), peak_rss_bytes=probe["peak_rss_bytes"], cold_sample=probe["samples"][0])
        rows.append(row)
        equivalent(rows)
        with (args.output / "matrix.jsonl").open("a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(f"{len(rows)}/24 {row['case']}: {statistics.median(s['total'] for s in row['samples']):.6f}s", flush=True)


def profile(args):
    """CPU profiles, never used as benchmark samples. Both threads are covered."""
    assert platform.python_implementation() == "CPython", "CPython diagnostic only"
    frozen, binaries = source_hash(), libraries()
    original = read(args.input)
    templates = Workload(original.servers, original.jobs[: args.jobs])
    extension()
    raw, path = (ROOT / ".runs" / f"profile-{os.getpid()}{s}" for s in (".source", ".simtrace"))
    raw.parent.mkdir(exist_ok=True)
    writer_profiles = []
    real = TraceRecorder._run

    def writer_run(recorder):
        profiler = cpu_profile.Profile(timer=time.thread_time)
        writer_profiles.append(profiler)
        try:
            return profiler.runcall(real, recorder)
        finally:
            # Finalize on the same thread and immediately: delayed finalization
            # would charge unrelated work (or another thread's clock) to setprofile.
            profiler.create_stats()

    # Warmup uses the exact path without instrumentation.
    sample(templates, args, raw, path, None)
    main = cpu_profile.Profile(timer=time.thread_time)
    result, jobs, decisions, timing, _ = main.runcall(sample, templates, args, raw, path, None)
    main.create_stats()
    main_fingerprint = fingerprint(result)
    # CPython 3.14 cProfile also observes other threads, making thread_time
    # invalid there. The standard Python profiler uses per-thread setprofile.
    # Keep separate passes so instrumentation perturbs only one side at a time.
    writer_timing = None
    TraceRecorder._run = writer_run
    try:
        if args.mode != "none":
            result, jobs, decisions, writer_timing, _ = sample(templates, args, raw, path, None)
            assert fingerprint(result) == main_fingerprint, "profile-pass trajectory mismatch"
    finally:
        TraceRecorder._run = real
    validate_result(Workload(templates.servers, jobs), result, wip_cap=12)
    if args.mode != "none":
        validate_output(path, jobs, result, mode=args.mode, backend=args.backend)

    # A third, unprofiled diagnostic pass measures complete output/compression
    # calls and the actual encoded snapshot sizes. These spans are not speed
    # samples; output calls include scheduling/page-cache effects, not fsync.
    spans, snapshots, replay = {}, [], [None]

    def span(name, call, *positional, **keywords):
        wall, cpu = time.perf_counter(), time.thread_time()
        value = call(*positional, **keywords)
        cpu, wall = time.thread_time() - cpu, time.perf_counter() - wall
        entry = spans.setdefault(name, dict(calls=0, wall=0.0, cpu=0.0))
        entry["calls"] += 1
        entry["wall"] += wall
        entry["cpu"] += cpu
        return value

    real_batch, real_pack, real_compress = (
        TraceRecorder._write_batch,
        production_writer.pack,
        production_writer.zlib.compress,
    )
    real_io = {name: getattr(FileSink, name) for name in ("write", "commit", "close")}

    def batch(recorder, item):
        if item.rtype is RecordType.CHUNK:
            state = recorder._replay
            snapshots.append(dict(entities=len(state), jobs=sum(key.startswith("job-") for key in state), bytes=0))
            replay[0] = state
        try:
            return real_batch(recorder, item)
        finally:
            replay[0] = None

    def snapshot_pack(value):
        if value is replay[0] and value is not None:
            encoded = span("snapshot_pack", real_pack, value)
            snapshots[-1]["bytes"] = len(encoded)
            return encoded
        return real_pack(value)

    diagnostic_timing = None
    if args.mode != "none":
        TraceRecorder._write_batch = batch
        production_writer.pack = snapshot_pack
        production_writer.zlib.compress = lambda *a, **kw: span("compress", real_compress, *a, **kw)
        for name, call in real_io.items():

            def instrumented(sink, *a, _name=name, _call=call, **kw):
                return span("file_" + _name, _call, sink, *a, **kw)

            setattr(FileSink, name, instrumented)
        try:
            diagnostic, jobs, _, diagnostic_timing, _ = sample(templates, args, raw, path, None)
            assert fingerprint(diagnostic) == main_fingerprint, "I/O diagnostic trajectory"
            validate_output(path, jobs, diagnostic, mode=args.mode, backend=args.backend)
        finally:
            TraceRecorder._write_batch, production_writer.pack = real_batch, real_pack
            production_writer.zlib.compress = real_compress
            for name, call in real_io.items():
                setattr(FileSink, name, call)

    def export(profiler):
        rows = []
        for (filename, line, name), (primitive, calls, self_cpu, cumulative, _) in profiler.stats.items():
            if filename.startswith(str(REPO)):
                filename = str(Path(filename).relative_to(REPO))
            elif "/site-packages/simpy/" in filename:
                filename = "simpy/" + filename.split("/site-packages/simpy/")[1]
            rows.append(
                dict(
                    file=filename,
                    line=line,
                    name=name,
                    primitive_calls=primitive,
                    calls=calls,
                    self_cpu=self_cpu,
                    cumulative_cpu=cumulative,
                )
            )
        return dict(
            total_self_cpu=sum(r["self_cpu"] for r in rows),
            functions=sorted(rows, key=lambda r: r["self_cpu"], reverse=True),
        )

    assert source_hash() == frozen and libraries() == binaries, "profile provenance changed"
    output = dict(
        study="direct-production-profile",
        backend=args.backend,
        producer=args.producer,
        mode=args.mode,
        jobs=args.jobs,
        source_sha256=frozen,
        libraries=binaries,
        input_sha256=static.file_hash(args.input),
        trajectory_sha256=fingerprint(result),
        clock="time.thread_time",
        timing=timing,
        writer_pass_timing=writer_timing,
        separate_thread_passes=True,
        diagnostic_timing=diagnostic_timing,
        spans=spans,
        snapshots=snapshots,
        simulation=export(main),
        writers=[export(p) for p in writer_profiles],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    raw.unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    print(
        f"profile {args.backend} {args.producer} {args.mode}: "
        f"simulation CPU {output['simulation']['total_self_cpu']:.3f}s, "
        f"writer CPU {sum(p['total_self_cpu'] for p in output['writers']):.3f}s",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("worker", "profile"):
        run = sub.add_parser(name)
        run.add_argument("--input", type=Path, required=True)
        run.add_argument("--jobs", type=int, default=5000)
        run.add_argument("--backend", choices=("native", "production"), required=True)
        run.add_argument("--producer", choices=("independent", "feedback"), required=True)
        run.add_argument("--mode", choices=("none", "kpi", "full"), required=True)
        run.add_argument("--warmups", type=int, default=1)
        run.add_argument("--repeats", type=int, default=5)
        run.add_argument("--probe", action="store_true")
        if name == "profile":
            run.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("matrix")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--pypy", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(worker(args)))
    elif args.command == "matrix":
        matrix(args)
    else:
        profile(args)


if __name__ == "__main__":
    main()
