"""Matched stock observations with no final trace, plus contemporary full controls."""

import argparse
from contextlib import contextmanager, nullcontext, redirect_stdout
import copy
from datetime import datetime, UTC
import gzip
import importlib.metadata
import importlib.util
import io
import itertools
import json
import math
import os
from pathlib import Path
import platform
import profile as cpu_profile
import random
import statistics
import subprocess
import sys
import tempfile
import time

from simulatte import Environment, Provenance, Trace, TraceRecorder
import simulatte.trace.writer as production_writer
from prototypes.native_engine.benchmark import file_hash
from prototypes.native_engine.model import Workload
from prototypes.native_engine.workloads import read
from .compiled import extension
from .feedback import validate_feed
from .observation_benchmark import case_id, sample
from .observation_gate import canonical, equivalent
from .production_benchmark import REPO, ROOT, PHASES, source_hash, sources
from .rich_recording import validate_records
from .scalar_benchmark import digest, libraries
from .trace_benchmark import normalized_wire
from .validation import fingerprint, validate_result

SEED = 20261102


def cases():
    return [
        dict(interpreter=i, producer=p, jobs=n, level=level, backend=b)
        for i, p, n, level, b in itertools.product(
            ("CPython", "PyPy"),
            ("independent", "feedback"),
            (5000, 20000),
            ("none", "full"),
            ("production", "buffered", "boundary"),
        )
    ]


@contextmanager
def forbid_recording():
    """Fail if a no-trace sample attempts recorder, digest or projection setup."""
    from . import direct_trace, production

    targets = [
        (direct_trace, "TraceRecorder"),
        (production, "TraceRecorder"),
        (Environment, "enable_digest"),
        (Environment, "request_projection"),
    ]
    originals = [(obj, name, getattr(obj, name)) for obj, name in targets]

    def forbidden(*args, **kwargs):
        raise AssertionError("recording, digest and semantic projection are disabled")

    try:
        for obj, name in targets:
            setattr(obj, name, forbidden)
        yield
    finally:
        for obj, name, original in originals:
            setattr(obj, name, original)


def trace_fields(path, observed, level, *, validate, signature=None):
    if level == "none":
        assert not path.exists(), "no-trace sample created a final trace"
        return dict(
            trace_digest=None,
            trace_wire_sha256=None,
            series_values_sha256=None,
            trace_signature=dict(chunks=0, domain=0, observers=0),
            trace_bytes=0,
        )
    trace = Trace.open(path)
    wire, chunks = normalized_wire(path)
    if validate:
        trace.check()
        assert trace.verify() is True and trace.outcome == "completed" and trace.manifest["complete"]
        equivalent(trace.kpis(), observed["scalars"])
        last = trace.state_at(trace.cursor_range[1])
        assert not any(key.startswith("job-") for key in last)
        assert last["shopfloor"]["jobs_in_system"] == 0 and last["psp"]["jobs"] == ()
    if signature is None:
        signature = dict(
            chunks=chunks, domain=sum(1 for _ in trace.events()), observers=sum(1 for _ in trace.observer_events())
        )
    assert signature["chunks"] == chunks
    return dict(
        trace_digest=trace.fingerprint.digest,
        trace_wire_sha256=wire,
        series_values_sha256=digest({k: [(c[0], v) for c, v in s] for k, s in trace.kpi_series().items()}),
        trace_signature=signature,
        trace_bytes=path.stat().st_size,
    )


def outputs(result, observed, jobs, decisions):
    return dict(
        trajectory=fingerprint(result),
        generated=digest([(j.arrival, j.route, j.durations, j.due) for j in jobs]),
        decisions=digest(decisions),
        observations_sha256=digest(canonical(observed)),
        scalars=observed["scalars"],
        model_kpis=result["kpis"],
    )


def worker(args):
    assert Path(production_writer.__file__).resolve() == REPO / "src/simulatte/trace/writer.py"
    frozen, binaries = source_hash(), libraries()
    original = read(args.input)
    templates = Workload(original.servers, original.jobs[: args.jobs])
    assert len(templates.jobs) == args.jobs
    extension()
    provenance = Provenance(
        model="native-stock-observation-v1",
        source=frozen,
        inputs=file_hash(args.input),
        dependencies=file_hash(REPO / "uv.lock"),
    )
    raw, path = (ROOT / ".runs" / f"untraced-{os.getpid()}{s}" for s in (".source", ".simtrace"))
    raw.parent.mkdir(exist_ok=True)
    warmup_count = 0 if args.probe else args.warmups
    warmups, samples = [], []
    expected, signature = None, None
    try:
        with forbid_recording() if args.level == "none" else nullcontext():
            for index in range(warmup_count + (1 if args.probe else args.repeats)):
                result, observed, jobs, decisions, timing, rss = sample(templates, args, raw, path, provenance)
                current = outputs(result, observed, jobs, decisions)
                current.update(trace_fields(path, observed, args.level, validate=expected is None, signature=signature))
                signature = current["trace_signature"]
                assert current["model_kpis"] is not None, "derived totals must remain timed in both modes"
                if expected is None:
                    validate_result(Workload(templates.servers, jobs), result, wip_cap=12)
                    if args.producer == "feedback":
                        validate_feed(templates, jobs, decisions, result)
                    assert observed["window"] == (0, result["makespan"], True)
                    if args.backend != "production":
                        validate_records(raw, Workload(templates.servers, jobs), result, cap=12, policy="spt")
                    payload = json.dumps(canonical(observed), separators=(",", ":")).encode()
                    args.observations.parent.mkdir(parents=True, exist_ok=True)
                    if args.observations.exists():
                        assert gzip.decompress(args.observations.read_bytes()) == payload, (
                            "recording changed stock output"
                        )
                    else:
                        args.observations.write_bytes(gzip.compress(payload, mtime=0))
                    expected = current
                assert current == expected, "within-case output drift"
                (warmups if index < warmup_count else samples).append(timing)
                counters = {k: result[k] for k in ("completed", "events", "callbacks", "records", "makespan")}
                operations = sum(len(j.route) for j in jobs)
                del result, observed, jobs, decisions
        assert source_hash() == frozen and libraries() == binaries
        return dict(
            interpreter=platform.python_implementation(),
            python=sys.version,
            versions={n: importlib.metadata.version(n) for n in ("simulatte", "simpy", "msgpack")},
            producer=args.producer,
            jobs=args.jobs,
            level=args.level,
            backend=args.backend,
            source_sha256=frozen,
            libraries=binaries,
            input_sha256=file_hash(args.input),
            no_recording_guard=args.level == "none",
            **expected,
            observations_file=str(args.observations),
            observations_file_sha256=file_hash(args.observations),
            operations=operations,
            counters=counters,
            source_bytes=raw.stat().st_size if raw.exists() else 0,
            warmups=warmups,
            samples=samples,
            peak_rss_bytes=rss if args.probe else None,
        )
    finally:
        raw.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


def check_groups(rows):
    groups, native, recording = {}, {}, {}
    for row in rows:
        prior = groups.setdefault((row["producer"], row["jobs"]), row)
        for field in ("trajectory", "generated", "decisions", "operations"):
            assert row[field] == prior[field], field
        equivalent(row["scalars"], prior["scalars"])
        equivalent(row["model_kpis"], prior["model_kpis"])
        key = row["interpreter"], row["producer"], row["jobs"], row["backend"] == "production"
        previous = recording.setdefault(key, row)
        assert row["observations_sha256"] == previous["observations_sha256"], "recording changed stock values"
        if row["backend"] != "production":
            key = row["interpreter"], row["producer"], row["jobs"], row["level"]
            previous = native.setdefault(key, row)
            for field in (
                "observations_sha256",
                "trace_digest",
                "trace_wire_sha256",
                "series_values_sha256",
                "trace_signature",
                "trace_bytes",
                "source_bytes",
                "counters",
            ):
                assert row[field] == previous[field], field


def metadata():
    path = REPO / "prototypes/native_engine/.inputs/jobs-50000-load-0.95.json"
    order = cases()
    random.Random(SEED).shuffle(order)
    return dict(
        created=datetime.now(UTC).isoformat(),
        source_sha256=source_hash(),
        files=sources(),
        libraries=libraries(),
        input=str(path.relative_to(REPO)),
        input_sha256=file_hash(path),
        seed=SEED,
        platform=platform.platform(),
        case_order=[case_id(c) for c in order],
        starting_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        tests={
            str(p.relative_to(REPO)): file_hash(p)
            for p in (ROOT / "tests/test_live_untraced.py", ROOT / "tests/test_live_observation.py")
        },
    )


def audit(directory):
    meta = json.loads((directory / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["source_sha256"] == source_hash() and meta["libraries"] == libraries()
    assert file_hash(REPO / meta["input"]) == meta["input_sha256"] and meta["seed"] == SEED
    for path, value in meta["tests"].items():
        assert file_hash(REPO / path) == value
    oldmeta = json.loads((ROOT / "observation-results/scaling/metadata.json").read_text())
    assert libraries() == oldmeta["libraries"], "native binaries changed"
    for name in ("csrc/dynamic_engine.c", "csrc/dynamic_engine.h", "csrc/scalar_module.c", "compiled.py", "native.py"):
        path = ROOT / name
        old = subprocess.check_output(["git", "show", f"d4aa1f2:{path.relative_to(REPO)}"], cwd=REPO)
        assert path.read_bytes() == old, "native engine/binding changed"
    order = cases()
    random.Random(SEED).shuffle(order)
    assert meta["case_order"] == [case_id(c) for c in order]
    rows = [json.loads(line) for line in (directory / "matrix.jsonl").read_text().splitlines()]
    assert [r["case"] for r in rows] == meta["case_order"]
    exports, reference = {}, {}
    for row, case in zip(rows, order):
        assert all(row[k] == v for k, v in case.items())
        for key in ("source_sha256", "libraries", "input_sha256"):
            assert row[key] == meta[key]
        assert len(row["samples"]) == 5 and len(row["warmups"]) == (1 if row["interpreter"] == "CPython" else 7)
        for timing in [*row["samples"], *row["warmups"], row["cold_sample"]]:
            assert all(math.isfinite(timing[k]) and timing[k] >= 0 for k in (*PHASES, "process_cpu"))
            assert abs(sum(timing[k] for k in PHASES[:-1]) - timing["total"]) < 1e-8
        assert row["peak_rss_bytes"] > 0 and row["counters"]["completed"] == row["jobs"]
        for key, value in row["cold_evidence"].items():
            assert row[key] == value, key
        path = REPO / row["observations_file"]
        assert file_hash(path) == row["observations_file_sha256"]
        if str(path) not in exports:
            exports[str(path)] = json.loads(gzip.decompress(path.read_bytes()))
        observed = exports[str(path)]
        assert digest(observed) == row["observations_sha256"]
        equivalent(observed["scalars"], row["scalars"])
        equivalent(reference.setdefault((row["producer"], row["jobs"]), observed), observed)
        assert row["no_recording_guard"] == (row["level"] == "none")
        if row["level"] == "none":
            assert row["trace_bytes"] == 0 and row["trace_digest"] is row["trace_wire_sha256"] is None
            assert row["series_values_sha256"] is None and row["trace_signature"] == dict(
                chunks=0, domain=0, observers=0
            )
        else:
            assert row["trace_bytes"] > 0 and row["trace_signature"]["domain"] == 7 * (row["jobs"] + row["operations"])
            assert row["trace_signature"]["observers"] == 4 * row["jobs"] + (row["backend"] != "production") * 2
        assert row["source_bytes"] == (
            0 if row["backend"] == "production" else 8 + 32 * (4 * row["jobs"] + 5 * row["operations"])
        )
    check_groups(rows)
    return meta, rows


def matrix(args):
    args.output.mkdir(parents=True, exist_ok=False)
    meta = metadata()
    (args.output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    order = cases()
    random.Random(SEED).shuffle(order)
    rows = []
    for case in order:
        name = "production" if case["backend"] == "production" else "native"
        observed = (
            args.output / "observations" / f"{case['interpreter']}-{case['producer']}-{case['jobs']}-{name}.json.gz"
        )
        command = [
            sys.executable if case["interpreter"] == "CPython" else str(args.pypy.absolute()),
            "-m",
            "prototypes.native_engine_dynamic.untraced_benchmark",
            "worker",
            "--input",
            str(REPO / meta["input"]),
            "--observations",
            str(observed),
            "--warmups",
            "1" if case["interpreter"] == "CPython" else "7",
        ]
        for key in ("producer", "jobs", "level", "backend"):
            command.extend([f"--{key}", str(case[key])])
        env = dict(os.environ, PYTHONHASHSEED="0")
        row = json.loads(subprocess.check_output(command, cwd=REPO, env=env, text=True))
        cold = json.loads(subprocess.check_output([*command, "--probe"], cwd=REPO, env=env, text=True))
        keys = [k for k in row if k not in ("samples", "warmups", "peak_rss_bytes")]
        assert all(row[k] == cold[k] for k in keys)
        assert all(row[k] == v for k, v in case.items())
        assert row["source_sha256"] == meta["source_sha256"] == source_hash() and row["libraries"] == meta["libraries"]
        assert row["input_sha256"] == meta["input_sha256"]
        row.update(
            case=case_id(case),
            cold_sample=cold["samples"][0],
            peak_rss_bytes=cold["peak_rss_bytes"],
            cold_evidence={k: cold[k] for k in keys},
        )
        rows.append(row)
        check_groups(rows)
        with (args.output / "matrix.jsonl").open("a") as target:
            target.write(json.dumps(row) + "\n")
        print(f"{len(rows)}/48 {row['case']} {statistics.median(s['total'] for s in row['samples']):.4f}s", flush=True)
    audit(args.output)
    result = dict(
        source_sha256=meta["source_sha256"],
        cases=len(rows),
        samples=sum(len(r["samples"]) for r in rows),
        cold_probes=len(rows),
        stock_values_equivalent=True,
        native_delivery_output_exact=True,
        evidence={name: file_hash(args.output / name) for name in ("metadata.json", "matrix.jsonl")},
    )
    (args.output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


def export_profile(profiler):
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


def profile(args):
    assert platform.python_implementation() == "CPython", "CPython thread-CPU diagnostic only"
    assert Path(production_writer.__file__).resolve() == REPO / "src/simulatte/trace/writer.py"
    frozen, binaries = source_hash(), libraries()
    original = read(args.input)
    templates = Workload(original.servers, original.jobs[: args.jobs])
    extension()
    raw, path = (ROOT / ".runs" / f"untraced-profile-{os.getpid()}{s}" for s in (".source", ".simtrace"))
    raw.parent.mkdir(exist_ok=True)
    provenance = Provenance(
        model="native-stock-observation-v1",
        source=frozen,
        inputs=file_hash(args.input),
        dependencies=file_hash(REPO / "uv.lock"),
    )
    writers = []
    real = TraceRecorder._run

    def writer_run(recorder):
        profiler = cpu_profile.Profile(timer=time.thread_time)
        writers.append(profiler)
        try:
            return profiler.runcall(real, recorder)
        finally:
            profiler.create_stats()

    try:
        with forbid_recording() if args.level == "none" else nullcontext():
            # Uninstrumented warm-up; profile values must match it exactly.
            result, observed, jobs, decisions, _, _ = sample(templates, args, raw, path, provenance)
            expected = outputs(result, observed, jobs, decisions)
            main = cpu_profile.Profile(timer=time.thread_time)
            result, observed, jobs, decisions, timing, _ = main.runcall(sample, templates, args, raw, path, provenance)
            main.create_stats()
            assert outputs(result, observed, jobs, decisions) == expected
            writer_timing = None
            # Python profile uses per-thread setprofile. Separate passes keep
            # a different thread's clock out of each instrumentation pass.
            if args.level != "none":
                TraceRecorder._run = writer_run
                try:
                    result, observed, jobs, decisions, writer_timing, _ = sample(templates, args, raw, path, provenance)
                    assert outputs(result, observed, jobs, decisions) == expected
                finally:
                    TraceRecorder._run = real
            validate_result(Workload(templates.servers, jobs), result, wip_cap=12)
            if args.producer == "feedback":
                validate_feed(templates, jobs, decisions, result)
            trace_fields(path, observed, args.level, validate=True)
            if args.backend != "production":
                validate_records(raw, Workload(templates.servers, jobs), result, cap=12, policy="spt")
        assert len(writers) == (0 if args.level == "none" else 1)
        assert source_hash() == frozen and libraries() == binaries
        output = dict(
            interpreter="CPython",
            producer=args.producer,
            jobs=args.jobs,
            level=args.level,
            backend=args.backend,
            source_sha256=frozen,
            libraries=binaries,
            input_sha256=file_hash(args.input),
            **expected,
            no_recording_guard=args.level == "none",
            clock="time.thread_time",
            separate_thread_passes=True,
            timing=timing,
            writer_pass_timing=writer_timing,
            simulation=export_profile(main),
            writers=[export_profile(p) for p in writers],
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2) + "\n")
        print(
            f"profile {args.backend} {args.producer} {args.level}: main {output['simulation']['total_self_cpu']:.3f}s, "
            f"writer {sum(p['total_self_cpu'] for p in output['writers']):.3f}s",
            flush=True,
        )
    finally:
        TraceRecorder._run = real
        raw.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


def audit_experiment(directory):
    from .untraced_gate import check_rows

    frozen = source_hash()
    gates = []
    for interpreter, name in (("CPython", "cpython"), ("PyPy", "pypy")):
        folder = directory / f"gate-{name}"
        meta = json.loads((folder / "metadata.json").read_text())
        cert = json.loads((folder / "verification.json").read_text())
        assert meta["files"] == sources() and meta["source_sha256"] == frozen and meta["libraries"] == libraries()
        assert meta["interpreter"] == interpreter and cert["configurations"] == 1536 and cert["model_runs"] == 3072
        for path, value in cert["evidence"].items():
            assert file_hash(folder / path) == value
        rows = [json.loads(line) for line in (folder / "rows.jsonl").read_text().splitlines()]
        check_rows(rows)
        old = [
            json.loads(line) for line in (ROOT / f"observation-results/gate-{name}/rows.jsonl").read_text().splitlines()
        ]
        assert [r for r in rows if r["level"] == "full"] == [r for r in old if r["level"] == "full"]
        gates.append(rows)
    assert gates[0] == gates[1]
    meta, rows = audit(directory / "scaling")
    historical_meta = json.loads((ROOT / "observation-results/scaling/metadata.json").read_text())
    historical = {**historical_meta["files"], **historical_meta["tests"]}
    historical.update(
        {str(p.relative_to(REPO)): file_hash(p) for p in (ROOT / "observation-results").rglob("*") if p.is_file()}
    )
    spec = importlib.util.spec_from_file_location(
        "production_analysis", REPO / "specs/research/native-engine-data/production_analysis.py"
    )
    analysis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analysis)
    analysis.verify_committed_files(historical, "d4aa1f2")
    old = {
        r["case"]: r
        for r in [
            json.loads(line) for line in (ROOT / "observation-results/scaling/matrix.jsonl").read_text().splitlines()
        ]
    }
    for row in rows:
        prior = old.get(row["case"])
        if prior:
            for key in (
                "trajectory",
                "generated",
                "decisions",
                "observations_sha256",
                "scalars",
                "operations",
                "series_values_sha256",
                "trace_digest",
                "trace_signature",
                "source_bytes",
                "counters",
            ):
                assert row[key] == prior[key], ("historical output drift", row["case"], key)
        prior = next(
            r
            for r in old.values()
            if r["interpreter"] == row["interpreter"]
            and r["producer"] == row["producer"]
            and r["jobs"] == row["jobs"]
            and (r["backend"] == "production") == (row["backend"] == "production")
        )
        assert row["observations_sha256"] == prior["observations_sha256"]
    # Default recorded output remains byte-for-byte equivalent with fixed provenance.
    from .trace_fixture import generate
    from .observation_fixture import generate as generate_observed

    with tempfile.TemporaryDirectory(prefix="untraced-default-") as tmp:
        with redirect_stdout(io.StringIO()):
            generate(Path(tmp), name="control")
        assert (Path(tmp) / "control.source").read_bytes() == (ROOT / "fixtures/semantics.source").read_bytes()
        assert normalized_wire(Path(tmp) / "control.simtrace") == normalized_wire(ROOT / "fixtures/semantics.simtrace")
        with redirect_stdout(io.StringIO()):
            generate_observed(Path(tmp))
        assert (Path(tmp) / "observed.source").read_bytes() == (ROOT / "fixtures/observed.source").read_bytes()
        assert normalized_wire(Path(tmp) / "observed.simtrace") == normalized_wire(ROOT / "fixtures/observed.simtrace")
    profiles = []
    for producer, level, backend in itertools.product(
        ("independent", "feedback"), ("none", "full"), ("production", "boundary")
    ):
        path = directory / "profiles" / f"{producer}-{level}-{backend}.json"
        value = json.loads(path.read_text())
        assert value["source_sha256"] == frozen and value["libraries"] == libraries()
        assert (value["producer"], value["level"], value["backend"], value["jobs"]) == (producer, level, backend, 5000)
        assert value["input_sha256"] == meta["input_sha256"] and value["clock"] == "time.thread_time"
        assert value["separate_thread_passes"] and len(value["writers"]) == (0 if level == "none" else 1)
        assert value["no_recording_guard"] == (level == "none")
        row = next(
            r
            for r in rows
            if r["interpreter"] == "CPython"
            and r["producer"] == producer
            and r["level"] == level
            and r["backend"] == backend
            and r["jobs"] == 5000
        )
        for key in ("trajectory", "generated", "decisions", "observations_sha256", "scalars", "model_kpis"):
            assert value[key] == row[key], ("profile output drift", key)
        for profile_data in [value["simulation"], *value["writers"]]:
            assert profile_data["total_self_cpu"] > 0
            assert math.isclose(sum(f["self_cpu"] for f in profile_data["functions"]), profile_data["total_self_cpu"])
            assert all(
                f["calls"] >= f["primitive_calls"] >= 0 and f["self_cpu"] >= 0 and f["cumulative_cpu"] >= 0
                for f in profile_data["functions"]
            )
        profiles.append(value)
    rejected = []
    for field in ("trajectory", "scalars", "observations_sha256", "trace_wire_sha256"):
        selection = [
            copy.deepcopy(r)
            for r in rows
            if r["interpreter"] == "CPython"
            and r["producer"] == "independent"
            and r["jobs"] == 5000
            and r["level"] == "full"
        ]
        next(r for r in selection if r["backend"] == "boundary")[field] = "corrupt"
        try:
            check_groups(selection)
        except (AssertionError, AttributeError):
            rejected.append(field)
        else:
            raise AssertionError(f"accepted corrupt {field}")
    summary = []
    for row in sorted(rows, key=case_id):
        times = [s["total"] for s in row["samples"]]
        summary.append(
            dict(
                case=row["case"],
                median_s=statistics.median(times),
                min_s=min(times),
                max_s=max(times),
                cold_s=row["cold_sample"]["total"],
                rss_mib=row["peak_rss_bytes"] / 2**20,
                trace_bytes=row["trace_bytes"],
                source_bytes=row["source_bytes"],
            )
        )
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    result = dict(
        source_sha256=frozen,
        gate_cases_per_interpreter=1536,
        model_runs_per_interpreter=3072,
        cross_interpreter_gate_exact=True,
        recording_observation_invariance=True,
        timing_cases=len(rows),
        timed_samples=sum(len(r["samples"]) for r in rows),
        cold_probes=len(rows),
        profiles=len(profiles),
        no_trace_guard_passed=True,
        native_engine_and_binaries_unchanged=True,
        default_recorded_output_preserved=True,
        observed_recorded_output_preserved=True,
        historical_full_output_preserved=True,
        historical_verified_files=len(historical),
        negative_checks_rejected=rejected,
        evidence={
            str(p.relative_to(directory)): file_hash(p)
            for p in sorted(directory.rglob("*"))
            if p.is_file() and p != directory / "verification.json"
        },
    )
    (directory / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "evidence"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("worker", "profile"):
        p = sub.add_parser(name)
        p.add_argument("--input", type=Path, required=True)
        p.add_argument("--jobs", type=int, default=5000)
        p.add_argument("--producer", choices=("independent", "feedback"), required=True)
        p.add_argument("--level", choices=("none", "full"), required=True)
        p.add_argument("--backend", choices=("production", "buffered", "boundary"), required=True)
        if name == "worker":
            p.add_argument("--observations", type=Path, required=True)
            p.add_argument("--warmups", type=int, default=1)
            p.add_argument("--repeats", type=int, default=5)
            p.add_argument("--probe", action="store_true")
        else:
            p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("matrix")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--pypy", type=Path, required=True)
    for name in ("audit", "audit-experiment"):
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(worker(args)))
    elif args.command == "profile":
        profile(args)
    elif args.command == "matrix":
        matrix(args)
    elif args.command == "audit-experiment":
        audit_experiment(args.output)
    else:
        _, rows = audit(args.output)
        print(json.dumps(dict(cases=len(rows))))


if __name__ == "__main__":
    main()
