"""Diagnostic measurements for the native-engine feasibility study; not a performance gate.

Run from any directory with the worktree's Python. Existing benchmark feeders own
the timed build/run/close interval. cProfile is a separate, distorted diagnostic
of the whole feeder call, including GC and final trajectory extraction. It profiles
only the calling thread, not the trace writer thread.
"""

from __future__ import annotations

import argparse
import cProfile
import dataclasses
import hashlib
import json
import platform
import pstats
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "benchmarks"))

import feeder  # noqa: E402
import intralogistics  # noqa: E402
import simpy  # noqa: E402
from run import provenance  # noqa: E402


def source_name(filename: str) -> str:
    for prefix, replacement in (
        (
            str(ROOT)
            + "/.venv/lib/python"
            + platform.python_version_tuple()[0]
            + "."
            + platform.python_version_tuple()[1]
            + "/site-packages/",
            "site-packages/",
        ),
        (str(ROOT) + "/", ""),
    ):
        if filename.startswith(prefix):
            return replacement + filename[len(prefix) :]
    return filename


def category(filename: str, function: str) -> str:
    if "/simpy/" in filename:
        return "simpy_python"
    if "_heapq." in function:
        return "heapq_native"
    if "of 'generator' objects" in function:
        return "generator_native"
    if "/simulatte/" in filename:
        module = filename.rsplit("/simulatte/", 1)[1]
        if module.startswith("trace/") or module in {
            "_wire.py",
            "digest.py",
            "events.py",
            "collectors.py",
            "kpi.py",
            "logsinks.py",
            "intralogistics/metrics.py",
        }:
            return "simulatte_observation_python"
        if module == "environment.py":
            return "simulatte_environment_python"
        return "simulatte_model_and_other_python"
    return "other_python_and_builtins"


def profile_summary(profiler: cProfile.Profile) -> dict:
    stats = pstats.Stats(profiler)
    groups: dict[str, float] = defaultdict(float)
    functions = []
    processed_events = 0
    for (filename, line, function), (primitive, calls, own, cumulative, _) in stats.stats.items():
        groups[category(filename, function)] += own
        if filename.endswith("simpy/core.py") and function == "step":
            processed_events += calls
        functions.append(
            {
                "file": source_name(filename),
                "line": line,
                "function": function,
                "primitive_calls": primitive,
                "calls": calls,
                "self_s": own,
                "cumulative_s": cumulative,
            }
        )
    return {
        "scope": "whole feeder call; calling thread only; cProfile overhead included",
        "total_profiled_s": stats.total_tt,
        "processed_events": processed_events,
        "groups": {key: {"self_s": value, "fraction": value / stats.total_tt} for key, value in sorted(groups.items())},
        "functions": sorted(functions, key=lambda row: row["self_s"], reverse=True),
    }


def identity(result: dict) -> tuple:
    if "counts" in result:
        return json.dumps(result["counts"], sort_keys=True), result["digest"]
    return (result["jobs_done"], result["operations_done"], result["trajectory"], result["digest"])


def trajectory(result: dict) -> str:
    return result.get("counts", result)["trajectory"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--shifts", type=float, default=2)
    args = parser.parse_args()
    if args.repeat < 1 or args.shifts <= 0:
        parser.error("repeat and shifts must be positive")
    output = {
        "measured_at": datetime.now(UTC).isoformat(),
        **provenance(),
        "python": sys.version,
        "simpy": simpy.__version__,
        "platform": platform.platform(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "method": {
            "warmup": 1,
            "repeat": args.repeat,
            "processes": 1,
            "timing": "existing feeder build/run/close wall time; JSON loading excluded",
            "profile": "one extra run per case, separately from unprofiled timings",
            "shifts": args.shifts,
            "agvs": 20,
            "interval": [30, 60],
        },
        "cases": [],
    }
    cases = [("jobshop-u90", mode) for mode in ("none", "kpi", "full")]
    cases += [("jobshop-u95", "none")]
    cases += [("intralogistics", mode) for mode in ("none", "kpi", "full")]
    with tempfile.TemporaryDirectory(prefix="simulatte-native-study-") as temporary:
        trace_path = Path(temporary) / "run.simtrace"
        for workload_name, mode in cases:
            if workload_name.startswith("jobshop"):
                workload = feeder.load(ROOT / "benchmarks/workloads" / f"jobshop10-{workload_name[-3:]}-5k.json")

                def run_case():
                    return dataclasses.asdict(feeder.run(workload, mode=mode, trace_path=trace_path))

                workload_metadata = {"sha256": workload.sha256, "params": workload.params, "horizon": workload.horizon}
            else:
                parameters = argparse.Namespace(shifts=args.shifts, agvs=20, interval_min=30, interval_max=60)

                def run_case():
                    return intralogistics.run(parameters, mode=mode, trace_path=trace_path)

                workload_metadata = {"seed": 42, "horizon": args.shifts * intralogistics.SHIFT}
            results = []
            reference = None
            for _ in range(args.repeat + 1):
                trace_path.unlink(missing_ok=True)
                result = run_case()
                if reference is None:
                    reference = identity(result)
                assert identity(result) == reference, (workload_name, mode, "repetition changed results")
                results.append(result)
            samples = [result["wall_s"] for result in results[1:]]
            trace_bytes = trace_path.stat().st_size if trace_path.exists() else None
            trace_path.unlink(missing_ok=True)
            profiler = cProfile.Profile()
            profiled_result = profiler.runcall(run_case)
            assert identity(profiled_result) == reference, (workload_name, mode, "profiling changed results")
            record = {
                "workload": workload_name,
                "mode": mode,
                "inputs": workload_metadata,
                "samples_s": samples,
                "median_s": statistics.median(samples),
                "min_s": min(samples),
                "max_s": max(samples),
                "result": results[-1],
                "trace_bytes": trace_bytes,
                "profile": profile_summary(profiler),
            }
            output["cases"].append(record)
            print(
                json.dumps(
                    {
                        "case": f"{workload_name}/{mode}",
                        "median_s": record["median_s"],
                        "groups": record["profile"]["groups"],
                    }
                ),
                flush=True,
            )
    for workload_name in {case["workload"] for case in output["cases"]}:
        cases_for_workload = [case for case in output["cases"] if case["workload"] == workload_name]
        assert len({trajectory(case["result"]) for case in cases_for_workload}) == 1
        digests = {case["result"]["digest"] for case in cases_for_workload if case["result"]["digest"]}
        assert len(digests) <= 1, (workload_name, "recording modes changed digest")
    output["invariance_checks"] = "passed: repeated/profiled runs, cross-mode trajectories, KPI/full digests"
    Path(__file__).with_name("measurements.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
