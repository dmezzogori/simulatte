"""Serial fresh-process hook and dynamic comparisons with equivalent lifecycle work."""

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

from prototypes.native_engine import benchmark as static_bench
from prototypes.native_engine.model import spt_hook
from prototypes.native_engine.validation import fingerprint as static_fingerprint
from prototypes.native_engine.validation import validate_recording as validate_static_recording
from prototypes.native_engine.validation import validate_result as validate_static_result
from prototypes.native_engine.workloads import read

from .validation import fingerprint, validate_recording, validate_result

REPO = Path(__file__).resolve().parents[2]
ROOT = Path(__file__).resolve().parent
HOOK_VARIANTS = (
    "native-builtin",
    "native-tuple",
    "native-buffers",
    "reference-builtin",
    "reference-tuple",
    "reference-buffers",
    "tuned-builtin",
    "tuned-tuple",
)
PHASES = ("build", "feed", "run", "extract", "close", "total")


def sources():
    paths = []
    for root in (REPO / "prototypes/native_engine", ROOT):
        paths.extend(root.glob("*.py"))
        paths.extend(root.glob("csrc/*.[ch]"))
    return {str(path.relative_to(REPO)): static_bench.file_hash(path) for path in sorted(paths)}


def source_hash():
    return hashlib.sha256(json.dumps(sources(), sort_keys=True).encode()).hexdigest()


def binary_hashes():
    from prototypes.native_engine.build import LIBRARY as STATIC_LIBRARY
    from .build import LIBRARY

    return {"static": static_bench.file_hash(STATIC_LIBRARY), "dynamic": static_bench.file_hash(LIBRARY)}


def factory(study, variant):
    if study == "hook":
        from prototypes.native_engine.hooks import BufferReferenceRunner
        from prototypes.native_engine.native import NativeRunner
        from prototypes.native_engine.reference import ReferenceRunner
        from prototypes.native_engine.tuned import TunedRunner

        return (
            {"native": NativeRunner, "reference": ReferenceRunner, "tuned": TunedRunner}[variant.split("-")[0]]
            if variant != "reference-buffers"
            else BufferReferenceRunner
        )
    from .native import NativeStream
    from .reference import ReferenceStream
    from .tuned import TunedStream

    return {"native": NativeStream, "reference": ReferenceStream, "tuned": TunedStream}[variant]


def sample(study, variant, cls, workload, mode, cap, path):
    if study == "hook":
        from prototypes.native_engine.hooks import buffer_spt

        hook = None if variant.endswith("builtin") else buffer_spt if variant.endswith("buffers") else spt_hook
        if variant == "native-buffers":

            def cls(workload, **options):
                from prototypes.native_engine.native import NativeRunner

                return NativeRunner(workload, hook_style="buffers", **options)

        result, timing, rss = static_bench.sample(cls, workload, "spt", mode, hook, path)
        timing["feed"] = 0.0
        return result, timing, rss
    gc.collect()
    begin = time.perf_counter()
    stream = cls(workload.servers, wip_cap=cap, policy="spt", mode=mode, trace_path=path if mode == "record" else None)
    built = time.perf_counter()
    try:
        # Every backend receives the same stream; all registration and advancement count.
        for job in workload.jobs:
            stream.advance(job.arrival)
            stream.add(job)
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
    timing = dict(
        build=built - begin,
        feed=fed - built,
        run=ran - fed,
        extract=extracted - ran,
        close=closed - extracted,
        total=closed - begin,
    )
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return result, timing, rss if sys.platform == "darwin" else rss * 1024


def worker(args):
    source, libraries, input_hash = source_hash(), binary_hashes(), static_bench.file_hash(args.input)
    workload = read(args.input)
    cls = factory(args.study, args.variant)
    ROOT.joinpath(".runs").mkdir(exist_ok=True)
    path = ROOT / ".runs" / f"{os.getpid()}.bin"
    warmups, samples, expected, recording = [], [], None, None
    repeats = 1 if args.probe else args.repeats
    warm_count = 0 if args.probe else args.warmups
    try:
        for index in range(warm_count + repeats):
            result, timing, rss = sample(args.study, args.variant, cls, workload, args.mode, args.cap, path)
            current = static_fingerprint(result) if args.study == "hook" else fingerprint(result)
            if expected is None:
                expected = current
                if args.study == "hook":
                    validate_static_result(workload, result)
                    if args.mode == "record":
                        validate_static_recording(workload, result, path)
                else:
                    validate_result(workload, result, wip_cap=args.cap)
                    if args.mode == "record":
                        validate_recording(workload, result, path, wip_cap=args.cap)
            assert current == expected, "trajectory drift"
            if args.mode == "record":
                current_record = static_bench.file_hash(path)
                if recording is None:
                    recording = current_record
                assert current_record == recording, "record drift"
            counters = {key: result[key] for key in ("completed", "events", "callbacks", "records", "makespan")}
            (warmups if index < warm_count else samples).append(timing)
            del result
        assert source_hash() == source and binary_hashes() == libraries, "worker provenance changed"
        assert static_bench.file_hash(args.input) == input_hash, "worker input changed"
        return dict(
            study=args.study,
            variant=args.variant,
            mode=args.mode,
            cap=args.cap,
            policy="spt",
            load=0.95,
            interpreter=platform.python_implementation(),
            python=sys.version,
            jobs=len(workload.jobs),
            operations=workload.operation_count,
            source_sha256=source,
            libraries=libraries,
            input_sha256=input_hash,
            trajectory_sha256=expected,
            recording_sha256=recording,
            recording_bytes=path.stat().st_size if args.mode == "record" else 0,
            counters=counters,
            warmups=warmups,
            samples=samples,
            peak_rss_bytes=rss if args.probe else None,
        )
    finally:
        path.unlink(missing_ok=True)


def equivalent(rows):
    seen = {}
    for row in rows:
        key = (row["study"], row["input_sha256"], row["cap"])
        group = seen.setdefault(key, {})
        for field in ("trajectory_sha256", "recording_sha256"):
            if row[field] is not None:
                assert group.setdefault(field, row[field]) == row[field], (key, field)


def matrix(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in args.output.read_text().splitlines()] if args.output.exists() else []
    source, libraries = source_hash(), binary_hashes()
    assert all(row["source_sha256"] == source and row["libraries"] == libraries for row in rows), (
        "provenance changed: use a new output"
    )
    done = {row["case"] for row in rows}
    studies = ("hook", "dynamic") if args.study == "all" else (args.study,)
    for study in studies:
        for label, executable in (("cpython", sys.executable), ("pypy", str(args.pypy.absolute()))):
            cases = (
                itertools.product((50000,), ("none", "record"), (None,), HOOK_VARIANTS)
                if study == "hook"
                else itertools.product((5000, 50000), ("none", "record"), (None, 12), ("reference", "tuned", "native"))
            )
            for jobs, mode, cap, variant in cases:
                case = f"{study}-{label}-{variant}-{jobs}-{mode}-{cap}"
                if case in done:
                    continue
                path = REPO / "prototypes/native_engine/.inputs" / f"jobs-{jobs}-load-0.95.json"
                input_hash = static_bench.file_hash(path)
                command = [
                    executable,
                    "-m",
                    "prototypes.native_engine_dynamic.benchmark",
                    "worker",
                    "--study",
                    study,
                    "--variant",
                    variant,
                    "--input",
                    str(path),
                    "--mode",
                    mode,
                    "--cap",
                    str(cap),
                    "--warmups",
                    "7" if label == "pypy" else "1",
                    "--repeats",
                    "5",
                ]
                env = dict(os.environ, PYTHONHASHSEED="0")
                row = json.loads(subprocess.check_output(command, cwd=REPO, env=env, text=True))
                probe = json.loads(subprocess.check_output([*command, "--probe"], cwd=REPO, env=env, text=True))
                for current in (row, probe):
                    assert current["source_sha256"] == source and current["libraries"] == libraries, (
                        "case provenance changed"
                    )
                    assert current["input_sha256"] == input_hash, "case input changed"
                assert source_hash() == source and binary_hashes() == libraries, "matrix provenance changed"
                assert static_bench.file_hash(path) == input_hash, "matrix input changed"
                equivalent([row, probe])
                assert row["counters"] == probe["counters"]
                row.update(case=case, peak_rss_bytes=probe["peak_rss_bytes"], cold_sample=probe["samples"][0])
                rows.append(row)
                equivalent(rows)
                with args.output.open("a") as stream:
                    stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                print(f"{case}: {statistics.median(s['total'] for s in row['samples']):.6f}s", flush=True)
    assert source_hash() == source and binary_hashes() == libraries, "final provenance changed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("worker")
    run.add_argument("--study", choices=["hook", "dynamic"], required=True)
    run.add_argument("--variant", required=True)
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--mode", choices=["none", "kpi", "record"], default="none")
    run.add_argument("--cap", type=lambda s: None if s == "None" else int(s), default=None)
    run.add_argument("--warmups", type=int, default=1)
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--probe", action="store_true")
    run = sub.add_parser("matrix")
    run.add_argument("--study", choices=["hook", "dynamic", "all"], default="all")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--pypy", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(worker(args)))
    else:
        matrix(args)


if __name__ == "__main__":
    main()
