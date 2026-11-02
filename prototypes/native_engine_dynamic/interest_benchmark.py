"""Paired subscriber-interest controls over the unchanged untraced observation workload."""

import argparse
import copy
from datetime import datetime, UTC
import gzip
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
import io

from prototypes.native_engine.benchmark import file_hash
from . import untraced_benchmark as baseline
from .observation_benchmark import case_id as base_case_id
from .observation_gate import equivalent
from .production_benchmark import REPO, ROOT, PHASES, source_hash, sources
from .scalar_benchmark import digest, libraries
from .trace_benchmark import normalized_wire

SEED = 20261102


def variants():
    return [("production", False), ("buffered", False), ("buffered", True), ("boundary", False), ("boundary", True)]


def cases():
    return [
        dict(
            interpreter=interpreter,
            producer=producer,
            jobs=jobs,
            level="none",
            backend=backend,
            interest_checks=interest,
        )
        for interpreter, producer, jobs, (backend, interest) in itertools.product(
            ("CPython", "PyPy"), ("independent", "feedback"), (5000, 20000), variants()
        )
    ]


def case_id(row):
    return base_case_id(row) + ("-filtered" if row["interest_checks"] else "-original")


def emission_count(row):
    if row["backend"] == "production":
        return 3 * row["jobs"] + 5 * row["operations"]
    if row["interest_checks"]:
        return 2 * row["jobs"] + 5 * row["operations"]
    return 5 * row["jobs"] + 7 * row["operations"]


def worker(args):
    args.emission_counts = []
    result = baseline.worker(args)
    assert len(args.emission_counts) == len(result["samples"]) + len(result["warmups"])
    assert len(set(args.emission_counts)) == 1
    result.update(interest_checks=args.interest_checks, python_emissions=args.emission_counts[0])
    assert result["python_emissions"] == emission_count(result)
    return result


def profile(args):
    args.emission_counts = []
    baseline.profile(args)
    value = json.loads(args.output.read_text())
    assert len(args.emission_counts) == 2 and len(set(args.emission_counts)) == 1
    value.update(interest_checks=args.interest_checks, python_emissions=args.emission_counts[0])
    measured = sum(
        f["calls"]
        for f in value["simulation"]["functions"]
        if f["file"] == "src/simulatte/environment.py" and f["name"] == "emit"
    )
    assert measured == value["python_emissions"]
    args.output.write_text(json.dumps(value, indent=2) + "\n")


def profile_cases():
    return [
        dict(producer=producer, backend=backend, interest_checks=interest, jobs=5000, level="none")
        for producer, (backend, interest) in itertools.product(
            ("independent", "feedback"), (("production", False), ("boundary", False), ("boundary", True))
        )
    ]


def profile_name(row):
    return f"{row['producer']}-{row['backend']}-{'filtered' if row['interest_checks'] else 'original'}.json"


def check_groups(rows):
    baseline.check_groups(rows)
    for row in rows:
        assert row["level"] == "none" and row["no_recording_guard"]
        assert row["python_emissions"] == emission_count(row)


def matrix(args):
    args.output.mkdir(parents=True, exist_ok=False)
    meta = baseline.metadata()
    order = cases()
    random.Random(SEED).shuffle(order)
    meta.update(
        created=datetime.now(UTC).isoformat(),
        case_order=[case_id(row) for row in order],
        seed=SEED,
        tests={
            str(p.relative_to(REPO)): file_hash(p)
            for p in (
                ROOT / "tests/test_interest.py",
                ROOT / "tests/test_live_untraced.py",
                ROOT / "tests/test_live_observation.py",
            )
        },
    )
    (args.output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    rows = []
    for case in order:
        name = "production" if case["backend"] == "production" else "native"
        observed = (
            args.output / "observations" / f"{case['interpreter']}-{case['producer']}-{case['jobs']}-{name}.json.gz"
        )
        command = [
            sys.executable if case["interpreter"] == "CPython" else str(args.pypy.absolute()),
            "-m",
            "prototypes.native_engine_dynamic.interest_benchmark",
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
        if case["interest_checks"]:
            command.append("--interest-checks")
        env = dict(os.environ, PYTHONHASHSEED="0")
        row = json.loads(subprocess.check_output(command, cwd=REPO, env=env, text=True))
        cold = json.loads(subprocess.check_output([*command, "--probe"], cwd=REPO, env=env, text=True))
        keys = [key for key in row if key not in ("samples", "warmups", "peak_rss_bytes")]
        assert all(row[key] == cold[key] for key in keys)
        assert all(row[key] == value for key, value in case.items())
        assert row["source_sha256"] == meta["source_sha256"] == source_hash() and row["libraries"] == meta["libraries"]
        assert row["input_sha256"] == meta["input_sha256"]
        row.update(
            case=case_id(case),
            cold_sample=cold["samples"][0],
            peak_rss_bytes=cold["peak_rss_bytes"],
            cold_evidence={key: cold[key] for key in keys},
        )
        rows.append(row)
        check_groups(rows)
        with (args.output / "matrix.jsonl").open("a") as target:
            target.write(json.dumps(row) + "\n")
        print(f"{len(rows)}/40 {row['case']} {statistics.median(s['total'] for s in row['samples']):.4f}s", flush=True)
    audit(args.output)
    result = dict(
        source_sha256=meta["source_sha256"],
        cases=len(rows),
        samples=sum(len(r["samples"]) for r in rows),
        cold_probes=len(rows),
        stock_values_equivalent=True,
        publication_counts_exact=True,
        evidence={name: file_hash(args.output / name) for name in ("metadata.json", "matrix.jsonl")},
    )
    (args.output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


def audit(directory):
    meta = json.loads((directory / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["source_sha256"] == source_hash() and meta["libraries"] == libraries()
    assert file_hash(REPO / meta["input"]) == meta["input_sha256"] and meta["seed"] == SEED
    for path, value in meta["tests"].items():
        assert file_hash(REPO / path) == value
    prior_meta = json.loads((ROOT / "untraced-results/scaling/metadata.json").read_text())
    assert libraries() == prior_meta["libraries"]
    for name in ("csrc/dynamic_engine.c", "csrc/dynamic_engine.h", "csrc/scalar_module.c", "compiled.py", "native.py"):
        path = ROOT / name
        assert path.read_bytes() == subprocess.check_output(
            ["git", "show", f"0bcaa5c:{path.relative_to(REPO)}"], cwd=REPO
        )
    order = cases()
    random.Random(SEED).shuffle(order)
    assert meta["case_order"] == [case_id(row) for row in order]
    rows = [json.loads(line) for line in (directory / "matrix.jsonl").read_text().splitlines()]
    assert [row["case"] for row in rows] == meta["case_order"]
    exports, reference = {}, {}
    old = [json.loads(line) for line in (ROOT / "untraced-results/scaling/matrix.jsonl").read_text().splitlines()]
    for row, case in zip(rows, order):
        assert all(row[key] == value for key, value in case.items())
        for key in ("source_sha256", "libraries", "input_sha256"):
            assert row[key] == meta[key]
        assert len(row["samples"]) == 5 and len(row["warmups"]) == (1 if row["interpreter"] == "CPython" else 7)
        for timing in [*row["samples"], *row["warmups"], row["cold_sample"]]:
            assert all(math.isfinite(timing[key]) and timing[key] >= 0 for key in (*PHASES, "process_cpu"))
            assert abs(sum(timing[key] for key in PHASES[:-1]) - timing["total"]) < 1e-8
        for key, value in row["cold_evidence"].items():
            assert row[key] == value
        assert row["peak_rss_bytes"] > 0 and row["counters"]["completed"] == row["jobs"]
        assert row["trace_bytes"] == 0 and row["trace_digest"] is row["trace_wire_sha256"] is None
        assert row["trace_signature"] == dict(chunks=0, domain=0, observers=0) and row["series_values_sha256"] is None
        assert row["source_bytes"] == (
            0 if row["backend"] == "production" else 8 + 32 * (4 * row["jobs"] + 5 * row["operations"])
        )
        path = REPO / row["observations_file"]
        assert file_hash(path) == row["observations_file_sha256"]
        if str(path) not in exports:
            exports[str(path)] = json.loads(gzip.decompress(path.read_bytes()))
        observed = exports[str(path)]
        assert digest(observed) == row["observations_sha256"]
        equivalent(observed["scalars"], row["scalars"])
        equivalent(reference.setdefault((row["producer"], row["jobs"]), observed), observed)
        prior = next(
            value
            for value in old
            if all(value[key] == row[key] for key in ("interpreter", "producer", "jobs", "level", "backend"))
        )
        for key in (
            "trajectory",
            "generated",
            "decisions",
            "observations_sha256",
            "scalars",
            "model_kpis",
            "operations",
            "source_bytes",
            "counters",
        ):
            assert row[key] == prior[key], ("historical output drift", row["case"], key)
    check_groups(rows)
    return meta, rows


def audit_experiment(directory):
    from .untraced_gate import check_rows

    frozen = source_hash()
    gates = []
    for interpreter, name in (("CPython", "cpython"), ("PyPy", "pypy")):
        folder = directory / f"gate-{name}"
        meta = json.loads((folder / "metadata.json").read_text())
        cert = json.loads((folder / "verification.json").read_text())
        assert meta["files"] == sources() and meta["source_sha256"] == frozen and meta["libraries"] == libraries()
        assert meta["interpreter"] == interpreter and meta["interest_checks"]
        assert cert["configurations"] == 1536 and cert["model_runs"] == 3072
        for path, value in cert["evidence"].items():
            assert file_hash(folder / path) == value
        rows = [json.loads(line) for line in (folder / "rows.jsonl").read_text().splitlines()]
        check_rows(rows)
        old = [
            json.loads(line) for line in (ROOT / f"untraced-results/gate-{name}/rows.jsonl").read_text().splitlines()
        ]
        assert rows == old, "filtered stock/probe gate drift"
        gates.append(rows)
    assert gates[0] == gates[1]
    meta, rows = audit(directory / "scaling")
    old_meta = json.loads((ROOT / "untraced-results/scaling/metadata.json").read_text())
    historical = {**old_meta["files"], **old_meta["tests"]}
    historical.update(
        {str(p.relative_to(REPO)): file_hash(p) for p in (ROOT / "untraced-results").rglob("*") if p.is_file()}
    )
    spec = importlib.util.spec_from_file_location(
        "production_analysis", REPO / "specs/research/native-engine-data/production_analysis.py"
    )
    analysis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analysis)
    analysis.verify_committed_files(historical, "0bcaa5c")
    from .trace_fixture import generate
    from .observation_fixture import generate as generate_observed

    with tempfile.TemporaryDirectory(prefix="interest-recorded-regression-") as tmp:
        with redirect_stdout(io.StringIO()):
            generate(Path(tmp), name="control")
            generate_observed(Path(tmp))
        for current, previous in (("control", "semantics"), ("observed", "observed")):
            assert (Path(tmp) / f"{current}.source").read_bytes() == (ROOT / f"fixtures/{previous}.source").read_bytes()
            assert normalized_wire(Path(tmp) / f"{current}.simtrace") == normalized_wire(
                ROOT / f"fixtures/{previous}.simtrace"
            )
    for config in profile_cases():
        value = json.loads((directory / "profiles" / profile_name(config)).read_text())
        assert all(value[key] == item for key, item in config.items())
        assert value["source_sha256"] == frozen and value["libraries"] == libraries()
        assert value["input_sha256"] == meta["input_sha256"] and value["no_recording_guard"]
        assert value["clock"] == "time.thread_time" and value["separate_thread_passes"] and not value["writers"]
        row = next(
            row
            for row in rows
            if row["interpreter"] == "CPython" and all(row[key] == item for key, item in config.items())
        )
        for key in (
            "trajectory",
            "generated",
            "decisions",
            "observations_sha256",
            "scalars",
            "model_kpis",
            "python_emissions",
        ):
            assert value[key] == row[key]
        data = value["simulation"]
        assert data["total_self_cpu"] > 0 and math.isclose(
            sum(f["self_cpu"] for f in data["functions"]), data["total_self_cpu"]
        )
        assert all(
            f["self_cpu"] >= 0 and f["cumulative_cpu"] >= 0 and f["calls"] >= f["primitive_calls"] >= 0
            for f in data["functions"]
        )
        assert (
            sum(
                f["calls"]
                for f in data["functions"]
                if f["file"] == "src/simulatte/environment.py" and f["name"] == "emit"
            )
            == row["python_emissions"]
        )
    rejected = []
    for field in ("trajectory", "scalars", "observations_sha256", "python_emissions"):
        selection = [
            copy.deepcopy(row)
            for row in rows
            if row["interpreter"] == "CPython" and row["producer"] == "independent" and row["jobs"] == 5000
        ]
        next(row for row in selection if row["backend"] == "boundary" and row["interest_checks"])[field] = "corrupt"
        try:
            check_groups(selection)
        except (AssertionError, AttributeError):
            rejected.append(field)
        else:
            raise AssertionError(f"accepted corrupt {field}")
    summary = []
    for row in sorted(rows, key=case_id):
        values = [s["total"] for s in row["samples"]]
        summary.append(
            dict(
                case=row["case"],
                median_s=statistics.median(values),
                min_s=min(values),
                max_s=max(values),
                cold_s=row["cold_sample"]["total"],
                rss_mib=row["peak_rss_bytes"] / 2**20,
                python_emissions=row["python_emissions"],
                source_bytes=row["source_bytes"],
            )
        )
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    result = dict(
        source_sha256=frozen,
        gate_cases_per_interpreter=1536,
        model_runs_per_interpreter=3072,
        filtered_gate_matches_original=True,
        cross_interpreter_gate_exact=True,
        timing_cases=len(rows),
        timed_samples=sum(len(row["samples"]) for row in rows),
        cold_probes=len(rows),
        profiles=len(profile_cases()),
        stock_values_equivalent=True,
        publication_counts_exact=True,
        native_engine_and_binaries_unchanged=True,
        recorded_fixtures_preserved=True,
        historical_verified_files=len(historical),
        negative_checks_rejected=rejected,
        evidence={
            str(p.relative_to(directory)): file_hash(p)
            for p in sorted(directory.rglob("*"))
            if p.is_file() and p != directory / "verification.json"
        },
    )
    (directory / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "evidence"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("worker", "profile"):
        command = sub.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--jobs", type=int, default=5000)
        command.add_argument("--producer", choices=("independent", "feedback"), required=True)
        command.add_argument("--backend", choices=("production", "buffered", "boundary"), required=True)
        command.add_argument("--level", choices=("none",), default="none")
        command.add_argument("--interest-checks", action="store_true")
        if name == "worker":
            command.add_argument("--observations", type=Path, required=True)
            command.add_argument("--warmups", type=int, default=1)
            command.add_argument("--repeats", type=int, default=5)
            command.add_argument("--probe", action="store_true")
        else:
            command.add_argument("--output", type=Path, required=True)
    command = sub.add_parser("matrix")
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--pypy", type=Path, required=True)
    for name in ("audit", "audit-experiment"):
        command = sub.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        assert args.backend != "production" or not args.interest_checks
        print(json.dumps(worker(args)))
    elif args.command == "profile":
        assert args.backend != "production" or not args.interest_checks
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
