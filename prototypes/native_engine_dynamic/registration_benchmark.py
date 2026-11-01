"""Measure registration at identical producer windows; eager feed isolates admission.

Inputs, imports and workload construction are outside timing. Window slicing,
all admission copies, handles, advancement, results and close/GC are inside.
"""

from __future__ import annotations

import argparse
from datetime import datetime, UTC
import gc
import itertools
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import subprocess
import sys
import time

from prototypes.native_engine import benchmark as static
from prototypes.native_engine.workloads import read

from .benchmark import REPO, ROOT, binary_hashes, equivalent, factory, source_hash, sources
from .validation import fingerprint, validate_recording, validate_result

PHASES = ("build", "feed", "run", "extract", "close", "total")
CONFIGURATIONS = tuple(
    ("interleaved", backend, "scalar", window)
    for backend, window in itertools.product(("native", "reference", "tuned"), (1, 32, 256))
) + (
    ("interleaved", "native", "bulk", 32),
    ("interleaved", "native", "bulk", 256),
    ("eager", "reference", "scalar", 256),
    ("eager", "tuned", "scalar", 256),
    ("eager", "native", "scalar", 256),
    ("eager", "native", "bulk", 32),
    ("eager", "native", "bulk", 256),
)


def cases():
    return [
        dict(interpreter=interpreter, schedule=schedule, backend=backend, method=method, window=window, mode=mode)
        for interpreter, (schedule, backend, method, window), mode in itertools.product(
            ("CPython", "PyPy"), CONFIGURATIONS, ("none", "record")
        )
    ]


def case_id(case):
    return "-".join(str(case[key]) for key in ("interpreter", "schedule", "backend", "method", "window", "mode"))


def sample(cls, workload, args, path):
    gc.collect()
    begin = time.perf_counter()
    stream = cls(
        workload.servers, wip_cap=12, policy="spt", mode=args.mode, trace_path=path if args.mode == "record" else None
    )
    built = time.perf_counter()
    registered = 0
    try:
        if args.schedule == "interleaved" and args.method == "scalar" and args.window == 1:
            # Keep the original dynamic-study driver as the streaming baseline.
            for job in workload.jobs:
                stream.advance(job.arrival)
                stream.add(job)
            registered = len(workload.jobs)
        else:
            for offset in range(0, len(workload.jobs), args.window):
                batch = workload.jobs[offset : offset + args.window]
                if args.schedule == "interleaved":
                    stream.advance(batch[0].arrival)
                if args.method == "bulk":
                    handles = stream.add_many(batch)
                    registered += len(handles)
                    del handles
                else:
                    for job in batch:
                        stream.add(job)
                    registered += len(batch)
                del batch
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
    assert registered == len(workload.jobs)
    marks = (begin, built, fed, ran, extracted, closed)
    timing = dict(zip(PHASES[:-1], (b - a for a, b in zip(marks, marks[1:]))))
    timing["total"] = closed - begin
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return result, timing, rss if sys.platform == "darwin" else rss * 1024


def worker(args):
    frozen, libraries, input_hash = source_hash(), binary_hashes(), static.file_hash(args.input)
    workload = read(args.input)
    cls = factory("dynamic", args.backend)
    (ROOT / ".runs").mkdir(exist_ok=True)
    path = ROOT / ".runs" / f"registration-{os.getpid()}.bin"
    expected = recorded = None
    samples, warmups = [], []
    count = 0 if args.probe else args.warmups
    try:
        for index in range(count + (1 if args.probe else args.repeats)):
            result, timing, rss = sample(cls, workload, args, path)
            current = fingerprint(result)
            if expected is None:
                validate_result(workload, result, wip_cap=12)
                if args.mode == "record":
                    validate_recording(workload, result, path, wip_cap=12)
                expected = current
            assert current == expected, "trajectory drift"
            if args.mode == "record":
                hashed = static.file_hash(path)
                recorded = hashed if recorded is None else recorded
                assert hashed == recorded, "record drift"
            counters = {key: result[key] for key in ("completed", "events", "callbacks", "records", "makespan")}
            (warmups if index < count else samples).append(timing)
            del result
        assert source_hash() == frozen and binary_hashes() == libraries, "worker provenance changed"
        assert static.file_hash(args.input) == input_hash, "worker input changed"
        windows = math.ceil(len(workload.jobs) / args.window)
        return dict(
            study="registration",
            interpreter=platform.python_implementation(),
            python=sys.version,
            schedule=args.schedule,
            backend=args.backend,
            method=args.method,
            window=args.window,
            mode=args.mode,
            jobs=len(workload.jobs),
            operations=workload.operation_count,
            cap=12,
            policy="spt",
            load=0.95,
            source_sha256=frozen,
            libraries=libraries,
            input_sha256=input_hash,
            trajectory_sha256=expected,
            recording_sha256=recorded,
            recording_bytes=path.stat().st_size if args.mode == "record" else 0,
            counters=counters,
            registration_calls=windows if args.method == "bulk" else len(workload.jobs),
            advance_calls=windows if args.schedule == "interleaved" else 0,
            warmups=warmups,
            samples=samples,
            peak_rss_bytes=rss if args.probe else None,
        )
    finally:
        path.unlink(missing_ok=True)


def matrix(args):
    args.output.mkdir(parents=True, exist_ok=False)
    path = REPO / "prototypes/native_engine/.inputs/jobs-50000-load-0.95.json"
    frozen, libraries, input_hash = source_hash(), binary_hashes(), static.file_hash(path)
    order = cases()
    random.Random(20261101).shuffle(order)
    meta = dict(
        created=datetime.now(UTC).isoformat(),
        source_sha256=frozen,
        files=sources(),
        libraries=libraries,
        input_sha256=input_hash,
        input=str(path.relative_to(REPO)),
        host=platform.node(),
        platform=platform.platform(),
        compiler=subprocess.check_output(["cc", "--version"], text=True),
        starting_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        case_order=[case_id(case) for case in order],
        seed=20261101,
        command=" ".join(sys.argv),
        notes="Serial fresh workers; per-case warm and cold probes; no fsync. Cap 12, 50k jobs.",
    )
    (args.output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    rows = []
    for case in order:
        executable = sys.executable if case["interpreter"] == "CPython" else str(args.pypy.absolute())
        command = [
            executable,
            "-m",
            "prototypes.native_engine_dynamic.registration_benchmark",
            "worker",
            "--input",
            str(path),
            "--warmups",
            "1" if case["interpreter"] == "CPython" else "7",
        ]
        for key in ("schedule", "backend", "method", "window", "mode"):
            command.extend([f"--{key}", str(case[key])])
        env = dict(os.environ, PYTHONHASHSEED="0")
        row = json.loads(subprocess.check_output(command, cwd=REPO, env=env, text=True))
        probe = json.loads(subprocess.check_output([*command, "--probe"], cwd=REPO, env=env, text=True))
        for current in (row, probe):
            assert current["source_sha256"] == frozen and current["libraries"] == libraries, "case provenance"
            assert current["input_sha256"] == input_hash, "case input"
            assert all(current[key] == value for key, value in case.items()), "case identity"
        equivalent([row, probe])
        assert row["counters"] == probe["counters"]
        assert source_hash() == frozen and binary_hashes() == libraries, "matrix provenance changed"
        assert static.file_hash(path) == input_hash, "matrix input changed"
        row.update(case=case_id(case), peak_rss_bytes=probe["peak_rss_bytes"], cold_sample=probe["samples"][0])
        rows.append(row)
        equivalent(rows)
        with (args.output / "matrix.jsonl").open("a") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(f"{len(rows)}/64 {row['case']}: {statistics.median(s['total'] for s in row['samples']):.6f}s", flush=True)
    assert source_hash() == frozen and binary_hashes() == libraries, "final provenance changed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("worker")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--schedule", choices=("interleaved", "eager"), required=True)
    run.add_argument("--backend", choices=("native", "reference", "tuned"), required=True)
    run.add_argument("--method", choices=("scalar", "bulk"), required=True)
    run.add_argument("--window", type=int, required=True)
    run.add_argument("--mode", choices=("none", "record"), required=True)
    run.add_argument("--warmups", type=int, default=1)
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--probe", action="store_true")
    run = sub.add_parser("matrix")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--pypy", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        if args.window < 1 or args.repeats < 1 or args.warmups < 0:
            parser.error("window/repeats must be positive, warmups nonnegative")
        if args.method == "bulk" and args.backend != "native":
            parser.error("bulk is a native registration interface")
        print(json.dumps(worker(args)))
    else:
        matrix(args)


if __name__ == "__main__":
    main()
