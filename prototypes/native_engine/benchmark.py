"""Serial, fresh-process equivalent-work benchmark; see README.md for commands."""

from __future__ import annotations

import argparse
import gc
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time

from .model import fifo_hook, spt_hook
from .validation import fingerprint, validate_recording, validate_result
from .workloads import ROOT, read

REPO = ROOT.parents[1]


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def source_hash():
    paths = sorted([*ROOT.glob("*.py"), *ROOT.glob("csrc/*.[ch]")])
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runner_class(backend):
    if backend == "simulatte":
        from .baseline import SimulatteRunner

        return SimulatteRunner
    if backend == "native":
        from .native import NativeRunner

        return NativeRunner
    if backend == "tuned":
        from .tuned import TunedRunner

        return TunedRunner
    from .reference import ReferenceRunner

    return ReferenceRunner


def sample(cls, workload, policy, mode, hook, path):
    gc.collect()
    begin = time.perf_counter()
    runner = cls(workload, policy=policy, mode=mode, hook=hook, trace_path=path if mode == "record" else None)
    built = time.perf_counter()
    try:
        runner.run()
        ran = time.perf_counter()
        result = runner.result()
        extracted = time.perf_counter()
    finally:
        runner.close()
    del runner
    # Include cyclic object cleanup (particularly the existing Simulatte graph) in close.
    gc.collect()
    closed = time.perf_counter()
    times = dict(
        build=built - begin, run=ran - built, extract=extracted - ran, close=closed - extracted, total=closed - begin
    )
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        rss *= 1024
    return result, times, rss


def worker(args):
    workload = read(args.input)
    cls = runner_class(args.backend)
    hook = (fifo_hook if args.policy == "fifo" else spt_hook) if args.hook == "python" else None
    ROOT.joinpath(".runs").mkdir(exist_ok=True)
    path = ROOT / ".runs" / f"bench-{os.getpid()}.nevt"
    samples, warmups, expected, record_hash, counters = [], [], None, None, None
    repeats = 1 if args.probe else args.repeats
    warm_count = 0 if args.probe else args.warmups
    try:
        for iteration in range(warm_count + repeats):
            result, timing, rss = sample(cls, workload, args.policy, args.mode, hook, path)
            # Verification runs outside timing and after the fresh-probe RSS capture.
            current = fingerprint(result)
            if expected is None:
                expected = current
                validate_result(workload, result)
                if args.mode == "record":
                    validate_recording(workload, result, path)
            assert current == expected, "nondeterministic trajectory"
            if args.mode == "record":
                current_record = file_hash(path)
                if record_hash is None:
                    record_hash = current_record
                assert current_record == record_hash, "nondeterministic recording"
            counters = {k: result[k] for k in ("completed", "events", "callbacks", "records", "makespan")}
            if iteration < warm_count:
                warmups.append(timing["total"])
            else:
                samples.append(timing)
            del result
        return dict(
            backend=args.backend,
            policy=args.policy,
            mode=args.mode,
            hook=args.hook,
            interpreter=platform.python_implementation(),
            python=sys.version,
            jobs=len(workload.jobs),
            operations=workload.operation_count,
            input_sha256=file_hash(args.input),
            source_sha256=source_hash(),
            trajectory_sha256=expected,
            recording_sha256=record_hash,
            recording_bytes=path.stat().st_size if args.mode == "record" else 0,
            counters=counters,
            warmup_totals=warmups,
            samples=samples,
            peak_rss_bytes=rss if args.probe else None,
        )
    finally:
        path.unlink(missing_ok=True)


def assert_equivalent(rows):
    seen = {}
    for row in rows:
        key = (row["input_sha256"], row["policy"])
        values = seen.setdefault(key, {})
        for field in ("trajectory_sha256", "recording_sha256"):
            if row[field] is not None:
                assert values.setdefault(field, row[field]) == row[field], f"equivalence failure: {key}, {field}"


def matrix(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in args.output.read_text().splitlines()] if args.output.exists() else []
    assert all(row["source_sha256"] == source_hash() for row in rows), "source changed; choose a new output file"
    completed = {row["case"] for row in rows}
    interpreters = [("cpython", sys.executable, args.backends)]
    if args.pypy:
        # Keep a venv executable's lexical path: resolving its symlink loses pyvenv.cfg.
        interpreters.append(("pypy", str(args.pypy.absolute()), args.pypy_backends))
    for label, executable, backends in interpreters:
        for jobs, load, policy, mode, hook, backend in itertools.product(
            args.jobs, args.loads, args.policies, args.modes, args.hooks, backends
        ):
            case = f"{label}-{backend}-{jobs}-{load}-{policy}-{mode}-{hook}"
            if case in completed:
                continue
            path = ROOT / ".inputs" / f"jobs-{jobs}-load-{load}.json"
            command = [
                executable,
                "-m",
                "prototypes.native_engine.benchmark",
                "worker",
                "--input",
                str(path),
                "--backend",
                backend,
                "--policy",
                policy,
                "--mode",
                mode,
                "--hook",
                hook,
                "--warmups",
                str(args.pypy_warmups if label == "pypy" else args.warmups),
                "--repeats",
                str(args.repeats),
            ]
            env = dict(os.environ, PYTHONHASHSEED="0")
            row = json.loads(subprocess.check_output(command, cwd=REPO, env=env, text=True))
            if not args.skip_memory:
                probe = json.loads(subprocess.check_output([*command, "--probe"], cwd=REPO, env=env, text=True))
                assert_equivalent([row, probe])
                row["peak_rss_bytes"] = probe["peak_rss_bytes"]
                row["cold_sample"] = probe["samples"][0]
            row["case"], row["load"] = case, load
            rows.append(row)
            assert_equivalent(rows)
            with args.output.open("a") as stream:
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            median = statistics.median(s["total"] for s in row["samples"])
            print(f"{case}: {median:.6f}s; {jobs / median:,.0f} jobs/s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("worker")
    sub.add_argument("--input", type=Path, required=True)
    sub.add_argument("--backend", choices=["simulatte", "reference", "native", "tuned"], required=True)
    sub.add_argument("--policy", choices=["fifo", "spt"], default="fifo")
    sub.add_argument("--mode", choices=["none", "kpi", "record"], default="none")
    sub.add_argument("--hook", choices=["builtin", "python"], default="builtin")
    sub.add_argument("--warmups", type=int, default=1)
    sub.add_argument("--repeats", type=int, default=3)
    sub.add_argument("--probe", action="store_true")
    sub = commands.add_parser("matrix")
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument("--jobs", type=int, nargs="+", default=[5000, 50000])
    sub.add_argument("--loads", type=float, nargs="+", default=[0.5, 0.95])
    sub.add_argument("--policies", nargs="+", default=["fifo", "spt"])
    sub.add_argument("--modes", nargs="+", default=["none", "kpi", "record"])
    sub.add_argument("--hooks", nargs="+", default=["builtin", "python"])
    sub.add_argument("--backends", nargs="+", default=["simulatte", "reference", "native"])
    sub.add_argument("--pypy", type=Path)
    sub.add_argument("--pypy-backends", nargs="+", default=["simulatte", "reference"])
    sub.add_argument("--warmups", type=int, default=1)
    sub.add_argument("--pypy-warmups", type=int, default=7)
    sub.add_argument("--repeats", type=int, default=3)
    sub.add_argument("--skip-memory", action="store_true")
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(worker(args)))
    else:
        matrix(args)


if __name__ == "__main__":
    main()
