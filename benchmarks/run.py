"""Time one recording mode on a pre-generated workload and write the results as JSON (spec §14).

Runs the workload ``--warmup`` times without recording the time (on PyPy this is the JIT warm-up), then
``--repeat`` times, each in a fresh environment, and reports the median and interquartile range of the wall time
(build + run + close). With ``--processes P`` this is done in P fresh interpreters in turn and the samples are
pooled. Peak memory comes from a separate process that runs the workload once. Mode ``full`` also
reports the size and chunk count of the trace and the latency of cold seeks into it.

Usage::

    python benchmarks/run.py --mode none --workload benchmarks/workloads/jobshop10-u90-ci.json \\
        --warmup 2 --repeat 10 --json none.json

Run it with the interpreter of the environment to measure (``simulatte==0.12.0`` or the branch); modes
``digest`` and ``full`` need the branch.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import platform
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import feeder


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("simulatte")
    except PackageNotFoundError:  # pragma: no cover - running from a source tree without metadata
        return "unknown"


def _cpu_model() -> str:
    """CPU model name, or ``platform.processor()`` when the OS does not expose it cheaply."""
    try:
        if sys.platform == "darwin":
            command = ["sysctl", "-n", "machdep.cpu.brand_string"]
            return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
        if sys.platform.startswith("linux"):
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor() or "unknown"


def _commit() -> str:
    """Commit of the benchmark checkout (the measured branch for ``head``): git, else ``GITHUB_SHA``."""
    try:
        command = ["git", "rev-parse", "HEAD"]
        cwd = Path(__file__).parent
        return subprocess.run(command, capture_output=True, text=True, check=True, cwd=cwd).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("GITHUB_SHA", "unknown")


def _log_level() -> str:
    """Default log level of the installed simulatte: ``SimLogger``'s class-level one in 0.12.0, else the default of
    ``Environment(log_level=...)``."""
    try:
        sim_logger = importlib.import_module("simulatte.logger").SimLogger  # simulatte 0.12.0
    except ModuleNotFoundError:
        from simulatte.environment import Environment

        return inspect.signature(Environment).parameters["log_level"].default
    return sim_logger.get_level()


def provenance() -> dict[str, Any]:
    """What C1.9 asks to record with the results besides the workload: commit, logging level, hardware class."""
    return {
        "commit": _commit(),
        "log_level": _log_level(),
        "hardware": {"machine": platform.machine(), "cpu": _cpu_model(), "cpus": os.cpu_count()},
    }


def _max_rss_mb() -> float:
    """Peak resident set size of this process so far, in MB (ru_maxrss is bytes on macOS, KiB elsewhere)."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1e6 if sys.platform == "darwin" else rss * 1024 / 1e6


def quartiles(samples: list[float]) -> tuple[float, float, float]:
    """First quartile, median and third quartile (inclusive method; a single sample gives three equal values)."""
    if len(samples) == 1:
        return samples[0], samples[0], samples[0]
    q1, q2, q3 = statistics.quantiles(samples, n=4, method="inclusive")
    return q1, q2, q3


def percentile(samples: list[float], p: int) -> float:
    if len(samples) == 1:
        return samples[0]
    return statistics.quantiles(samples, n=100, method="inclusive")[p - 1]


def measure_seeks(path: Path, *, count: int, seed: int) -> dict[str, float]:
    """Latency of `count` cold seeks (empty chunk cache) to uniformly random times of the trace at `path`."""
    from simulatte.trace import Trace

    start = time.perf_counter()
    trace = Trace.open(path)
    open_ms = (time.perf_counter() - start) * 1000
    bounds = trace.cursor_range
    assert bounds is not None
    (t0, _), (t1, _) = bounds
    rng = random.Random(seed)
    latencies = []
    for _ in range(count):
        cursor = (rng.uniform(t0, t1), 0)
        trace._cache.clear()  # cold: the chunk is read, CRC-checked and decoded on every seek
        start = time.perf_counter()
        trace.state_at(cursor)
        latencies.append((time.perf_counter() - start) * 1000)
    return {
        "open_ms": open_ms,
        "seek_p50_ms": percentile(latencies, 50),
        "seek_p95_ms": percentile(latencies, 95),
        "seek_max_ms": max(latencies),
        "seeks": count,
    }


def memory_probe(workload_path: str, mode: str) -> dict[str, float]:
    """Run the workload once in this (fresh) process and report the peak RSS before and after."""
    workload = feeder.load(workload_path)
    before = _max_rss_mb()
    with tempfile.TemporaryDirectory() as tmp:
        feeder.run(workload, mode=mode, trace_path=Path(tmp) / "probe.simtrace")
    return {"rss_before_mb": before, "peak_mb": _max_rss_mb()}


def _peak_memory(workload_path: str, mode: str) -> dict[str, float]:
    completed = subprocess.run(
        [sys.executable, __file__, "--memory-probe", "--mode", mode, "--workload", workload_path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _in_process(args: argparse.Namespace) -> dict[str, Any]:
    """Warm-up and timed runs in this process."""
    workload = feeder.load(args.workload)
    with tempfile.TemporaryDirectory() as tmp:
        trace_path = Path(args.trace or Path(tmp) / "bench.simtrace")
        samples = []
        results = []
        for i in range(args.warmup + args.repeat):
            if trace_path.exists():
                trace_path.unlink()
            result = feeder.run(workload, mode=args.mode, trace_path=trace_path)
            if i >= args.warmup:
                samples.append(result.wall_s)
                results.append(result)
            print(f"{'warmup' if i < args.warmup else 'run'} {i + 1}: {result.wall_s:.4f} s", file=sys.stderr)
        if len({(r.digest, r.trajectory) for r in results}) != 1:
            raise RuntimeError("repeated runs gave different digests or trajectories")
        last = results[-1]
        q1, median, q3 = quartiles(samples)
        out: dict[str, Any] = {
            "label": args.label or feeder.default_label(),
            "simulatte_version": _version(),
            "has_trace": feeder.has_trace(),
            "mode": args.mode,
            "python": {
                "implementation": sys.implementation.name,
                "version": platform.python_version(),
                "build": platform.python_build()[0],
            },
            "platform": {"system": platform.system(), "machine": platform.machine(), "node": platform.node()},
            **provenance(),
            "workload": {
                "path": workload.path,
                "sha256": workload.sha256,
                "params": workload.params,
                "horizon": workload.horizon,
            },
            "counts": {
                "jobs_fed": last.jobs_fed,
                "operations_fed": last.operations_fed,
                "jobs_done": last.jobs_done,
                "operations_done": last.operations_done,
                "trajectory": last.trajectory,
            },
            "subscribers": last.subscribers,
            "digest": last.digest,
            "warmup": args.warmup,
            "repeat": args.repeat,
            "processes": 1,
            "process_medians_s": [median],
            "samples_s": samples,
            "median_s": median,
            "iqr_s": q3 - q1,
            "min_s": min(samples),
            "max_s": max(samples),
            "peak_mb": None,
            "rss_before_mb": None,
            "trace_bytes": None,
            "chunks": None,
            "open_ms": None,
            "seek_p50_ms": None,
            "seek_p95_ms": None,
            "seek_max_ms": None,
            "seeks": None,
        }
        if args.mode == "full":
            from simulatte.trace import Trace

            out["trace_bytes"] = os.path.getsize(trace_path)
            out["chunks"] = len(Trace.open(trace_path).index)
            if args.seeks:
                out.update(measure_seeks(trace_path, count=args.seeks, seed=args.seek_seed))
    return out


def _multi_process(args: argparse.Namespace) -> dict[str, Any]:
    """Run `_in_process` in ``--processes`` fresh interpreters, one after the other, and pool their samples.

    On PyPy the JIT settles into a different speed in different processes (about ±10 % on the CI workload), so a
    single process is not a representative sample. The first child also measures the trace and the seeks.
    """
    outs = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(args.processes):
            path = Path(tmp) / f"child{i}.json"
            command = [sys.executable, __file__, "--mode", args.mode, "--workload", args.workload]
            command += ["--warmup", str(args.warmup), "--repeat", str(args.repeat), "--processes", "1"]
            command += ["--no-memory", "--json", str(path), "--seek-seed", str(args.seek_seed)]
            command += ["--seeks", str(args.seeks if i == 0 else 0)]
            if args.label:
                command += ["--label", args.label]
            if args.trace and i == 0:
                command += ["--trace", args.trace]
            subprocess.run(command, check=True)
            outs.append(json.loads(path.read_text()))
    if len({(o["counts"]["trajectory"], o["digest"]) for o in outs}) != 1:
        raise RuntimeError("processes gave different digests or trajectories")
    out = outs[0]
    samples = [s for o in outs for s in o["samples_s"]]
    q1, median, q3 = quartiles(samples)
    out.update(
        processes=args.processes,
        process_medians_s=[o["median_s"] for o in outs],
        samples_s=samples,
        median_s=median,
        iqr_s=q3 - q1,
        min_s=min(samples),
        max_s=max(samples),
    )
    return out


def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    out = _in_process(args) if args.processes == 1 else _multi_process(args)
    if args.memory:
        out.update(_peak_memory(args.workload, args.mode))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=feeder.MODES, default="none")
    parser.add_argument("--workload", required=True)
    parser.add_argument("--warmup", type=int, default=2, help="untimed runs first (JIT warm-up on PyPy)")
    parser.add_argument("--repeat", type=int, default=10, help="timed runs (per process)")
    parser.add_argument(
        "--processes", type=int, default=1, help="repeat warm-up and timed runs in this many fresh processes"
    )
    parser.add_argument("--json", help="write the results here (default: stdout)")
    parser.add_argument("--label", help="name of the measured version in reports")
    parser.add_argument("--seeks", type=int, default=200, help="cold seeks measured in mode full (0 to skip)")
    parser.add_argument("--seek-seed", type=int, default=0)
    parser.add_argument("--trace", help="mode full: keep the trace of the last run at this path")
    parser.add_argument(
        "--memory", action=argparse.BooleanOptionalAction, default=True, help="measure peak RSS in a separate run"
    )
    parser.add_argument("--memory-probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.memory_probe:
        json.dump(memory_probe(args.workload, args.mode), sys.stdout)
        return 0
    if args.repeat < 1 or args.warmup < 0 or args.processes < 1:
        parser.error("--repeat and --processes must be positive and --warmup non-negative")
    out = benchmark(args)
    text = json.dumps(out, indent=2)
    if args.json:
        Path(args.json).write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
