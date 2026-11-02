"""Explicit backend/state-fingerprint gate; historical evidence stays immutable."""

import argparse
import copy
from dataclasses import replace
from datetime import datetime, UTC
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.execution.store import RunStore, _read
from simulatte.experiments import Experiment, Explicit, Replications, run
from simulatte.stopping import Horizon, FinitePopulation
from prototypes.native_engine.benchmark import file_hash
from prototypes.native_engine.workloads import generate
from . import execution_experiment as prior
from .compiled import CompiledStream
from .execution_project import ROOT, REPO, MODEL, create_project
from .scalar_benchmark import digest, libraries
from .state_codec import decode
from .validation import validate_result

SEED = 20261102


def sources():
    extra = (
        "specs/research/2026-11-02-native-backend-plan.md",
        "docs/guides/execution-backends.md",
        "zensical.toml",
        "tests/execution/test_backend.py",
    )
    return {**prior.sources(), **{name: file_hash(REPO / name) for name in extra}}


def metadata():
    value = prior.metadata()
    files = sources()
    value.update(
        created=datetime.now(UTC).isoformat(),
        files=files,
        source_sha256=digest(files),
        libraries=libraries(),
        kernel_changed="pure state getter only; transitions unchanged",
        production_code_changed="opt-in backend lifecycle/projection",
        fingerprint_wire_schema_changed=False,
    )
    return value


def read_results(project, run_id, *, baseline=None):
    result = prior.read_results(project, run_id, baseline=baseline)
    with RunStore.open(project / ".simulatte") as store:
        stored = store.replications(run_id)
        for row, item in zip(result["replications"], stored):
            assert (row["configuration"], row["index"]) == (item["configuration"], item["index"])
            committed = store.result(item["attempt"])
            trace = Trace.open(committed.path / "run.simtrace")
            initial = trace._initial["shopfloor"]
            capabilities = dict(committed.manifest["resolved"]["capabilities"]["execution_backend"])
            expected = "native-production-state-v1" if row["backend"] == "native" else "production-domain-state-v1"
            assert initial["$kind"] == "execution_backend"
            assert initial["fingerprint_schema"] == capabilities["state_schema"] == expected
            assert capabilities["state_projection"] == "opaque_digest"
            assert row["backend_state"]["schema"] == expected and len(row["backend_state"]["digest"]) == 64
            assert initial["state_digest"] != row["backend_state"]["digest"]
            assert trace.fingerprint.digest == committed.fingerprint["digest"]
            row.update(
                fingerprint_digest=committed.fingerprint["digest"],
                initial_state_digest=initial["state_digest"],
                source_hash=committed.manifest["request"]["source_hash"],
            )
    return result


def gate(output):
    output.mkdir(parents=True, exist_ok=False)
    meta, rows = metadata(), []
    with tempfile.TemporaryDirectory(prefix="native-backend-gate-") as tmp:
        project = create_project(Path(tmp) / "project")
        meta["native_assets"] = json.loads((project / "native-assets.json").read_text())
        for producer, policy, stopping in itertools.product(
            ("independent", "feedback"),
            ("fifo", "spt"),
            (Horizon(1), Horizon(10, warmup=2), Horizon(200, warmup=20)),
        ):
            params = [
                dict(backend=b, producer=producer, policy=policy, jobs=40, servers=3, wip_cap=2)
                for b in ("production", "native")
            ]
            experiment = prior.definition(params, stopping=stopping)
            summary = run(experiment, project=project, slots=SlotConfig(local=2), timeout=30)
            observed = read_results(project, summary["run_id"], baseline="production/base")
            prior.same_outputs(observed["replications"])
            rows.append(dict(producer=producer, policy=policy, stopping=stopping.to_dict(), **observed))
            print(f"backend gate {len(rows)}/12 exact shared outcomes, state projections checked", flush=True)
        assert run(experiment, project=project, slots=SlotConfig(local=2), timeout=30)["run_id"] == rows[-1]["run_id"]
        reused = run(
            replace(experiment, name="explicit backend reuse"), project=project, slots=SlotConfig(local=2), timeout=30
        )
        assert reused["counts"]["reused"] == 6
        reproduced = []
        spec = discover_project(project / MODEL.partition(":")[0], project)
        with Coordinator(spec, reference=MODEL) as coordinator:
            for original in rows[-1]["replications"][:2]:
                repeated = coordinator.reproduce(original["request_id"], timeout=30)
                assert repeated.fingerprint["digest"] == original["fingerprint_digest"]
                reproduced.append(original["backend"])
        probe = Experiment(
            name="future state sensitivity",
            model=MODEL,
            replications=Replications(count=2, base_seed=100),
            stopping=Horizon(1),
            configurations=Explicit(
                [dict(label=str(n), parameters=dict(backend="native", jobs=n, servers=3)) for n in (20, 21)]
            ),
        )
        status = run(probe, project=project, slots=SlotConfig(local=2), timeout=30)
        hidden = read_results(project, status["run_id"])
        prior.same_outputs(hidden["replications"])
        for index in (0, 1):
            first, second = [r for r in hidden["replications"] if r["index"] == index]
            assert first["kpis"] == second["kpis"] and first["proof"] == second["proof"]
            assert first["backend_state"] != second["backend_state"]
            assert first["fingerprint_digest"] != second["fingerprint_digest"]
        rejected = []
        for feature in ("full", "population", "unknown_kpi"):
            bad = prior.definition([dict(backend="native", jobs=2)], count=1)
            if feature == "full":
                bad = replace(bad, level="full")
            elif feature == "population":
                bad = replace(bad, stopping=FinitePopulation(arrivals=2, safety_limit=10))
            else:
                bad = replace(bad, kpis=("shopfloor/unknown",))
            failed = run(bad, project=project, slots=SlotConfig(local=1), timeout=30)
            assert failed["counts"]["failed"] == 1
            with RunStore.open(project / ".simulatte") as store:
                item = store.replications(failed["run_id"])[0]
                with store._transaction() as db:
                    details = _read(
                        db.execute("SELECT details FROM attempts WHERE id=?", (item["attempt"],)).fetchone()[0]
                    )
                    assert (
                        db.execute("SELECT COUNT(*) FROM results WHERE attempt=?", (item["attempt"],)).fetchone()[0]
                        == 0
                    )
                assert "unsupported" in details["message"] or "Unknown requested KPI" in details["message"]
                rejected.append(dict(feature=feature, message=details["message"]))
    assert sources() == meta["files"] and libraries() == meta["libraries"]
    (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    (output / "runs.json").write_text(json.dumps(rows, indent=2) + "\n")
    (output / "sensitivity.json").write_text(json.dumps(hidden, indent=2) + "\n")
    certificate = dict(
        main_replications=72,
        sensitivity_replications=4,
        reproduced_backends=reproduced,
        reused=6,
        ordinary_request_reuse=True,
        rejected=rejected,
        exact_shared_outcomes=True,
        state_projection_checked=True,
        source_sha256=meta["source_sha256"],
    )
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(certificate), flush=True)


def worker(args):
    experiment = prior.definition([dict(backend=args.backend, producer=args.producer, jobs=args.jobs, verify=False)])
    started = time.perf_counter()
    summary = run(experiment, project=args.project, slots=SlotConfig(local=1), timeout=60)
    execution_s = time.perf_counter() - started
    assert summary["counts"]["succeeded"] == 3
    started = time.perf_counter()
    observed = read_results(args.project, summary["run_id"])
    return dict(
        backend=args.backend,
        producer=args.producer,
        jobs=args.jobs,
        execution_s=execution_s,
        validation_reporting_s=time.perf_counter() - started,
        **observed,
    )


def check_rows(rows):
    prior.check_matrix(rows)
    groups = {}
    for row in rows:
        values = [
            (r["seed"], r["backend_state"], r["initial_state_digest"], r["fingerprint_digest"])
            for r in row["replications"]
        ]
        key = row["backend"], row["producer"], row["jobs"]
        assert groups.setdefault(key, values) == values, key


def state_cost(output):
    rows = []
    for n in (100, 5000, 20000):
        workload = generate(n, 0.95)
        stream = CompiledStream(workload.servers, wip_cap=12, policy="spt")
        try:
            for job in workload.jobs:
                stream.add(job)
            stream.advance(workload.jobs[n // 2].arrival)
            raw = stream.state_bytes()
            expected, size, now = hashlib.sha256(raw).hexdigest(), len(raw), stream.now
            decoded = decode(raw)
            del raw
            samples = []
            for _ in range(7):
                started = time.perf_counter()
                raw = stream.state_bytes()
                actual = hashlib.sha256(raw).hexdigest()
                samples.append(time.perf_counter() - started)
                assert actual == expected and stream.now == now
                del raw
            stream.run()
            validate_result(workload, stream.result(), wip_cap=12)
            rows.append(
                dict(
                    jobs=n,
                    operations=workload.operation_count,
                    bytes=size,
                    calendar=len(decoded["calendar"]),
                    pool=len(decoded["pool"]),
                    sha256=expected,
                    samples_s=samples,
                    median_s=statistics.median(samples),
                )
            )
        finally:
            stream.close()
    (output / "state-cost.json").write_text(json.dumps(rows, indent=2) + "\n")


def matrix(output):
    output.mkdir(parents=True, exist_ok=False)
    meta, order, rows = metadata(), prior.cases(), []
    random.Random(SEED).shuffle(order)
    meta.update(
        case_order=order,
        seed=SEED,
        timing=(
            "whole fresh-process experiment, three replications, including state fingerprinting; "
            "native compilation/project copy excluded"
        ),
    )
    state_cost(output)
    with tempfile.TemporaryDirectory(prefix="native-backend-timing-") as tmp:
        template = create_project(Path(tmp) / "template")
        meta["native_assets"] = json.loads((template / "native-assets.json").read_text())
        (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        for i, case in enumerate(order):
            project = Path(tmp) / str(i)
            shutil.copytree(template, project)
            command = [
                sys.executable,
                "-m",
                "prototypes.native_engine_dynamic.backend_experiment",
                "worker",
                "--project",
                str(project),
            ]
            for key in ("backend", "producer", "jobs"):
                command.extend(["--" + key, str(case[key])])
            started = time.perf_counter()
            row = json.loads(
                subprocess.check_output(command, cwd=REPO, text=True, env=dict(os.environ, PYTHONHASHSEED="0"))
            )
            row.update(repeat=case["repeat"], process_s=time.perf_counter() - started)
            rows.append(row)
            check_rows(rows)
            with (output / "matrix.jsonl").open("a") as target:
                target.write(json.dumps(row) + "\n")
            print(f"{i + 1}/24 {case} {row['process_s']:.3f}s", flush=True)
            shutil.rmtree(project)
    audit(output)


def audit(output):
    meta = json.loads((output / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["source_sha256"] == digest(sources())
    assert meta["libraries"] == libraries()
    rows = [json.loads(x) for x in (output / "matrix.jsonl").read_text().splitlines()]
    order = prior.cases()
    random.Random(SEED).shuffle(order)
    assert meta["case_order"] == order and len(rows) == 24
    for row, case in zip(rows, order):
        assert all(row[k] == v for k, v in case.items())
        assert len(row["replications"]) == 3 and row["process_s"] >= row["execution_s"] > 0
        assert all(
            len(r["kpis"]) == 8 and r["admitted"] == row["jobs"] and r["proof"] is None for r in row["replications"]
        )
    check_rows(rows)
    costs = json.loads((output / "state-cost.json").read_text())
    assert [r["jobs"] for r in costs] == [100, 5000, 20000] and all(len(r["samples_s"]) == 7 for r in costs)
    summary = []
    for producer, n in itertools.product(("independent", "feedback"), (5000, 20000)):
        selected = [r for r in rows if r["producer"] == producer and r["jobs"] == n]
        values = {
            b: statistics.median(r["process_s"] for r in selected if r["backend"] == b)
            for b in ("production", "native")
        }
        summary.append(
            dict(
                producer=producer,
                jobs=n,
                production_s=values["production"],
                native_s=values["native"],
                speedup=values["production"] / values["native"],
            )
        )
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    certificate = dict(
        source_sha256=meta["source_sha256"],
        experiments=24,
        replications=72,
        exact_shared_outputs=True,
        fingerprints_repeat_exactly=True,
        evidence={
            p: file_hash(output / p) for p in ("metadata.json", "matrix.jsonl", "summary.json", "state-cost.json")
        },
    )
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(certificate), flush=True)


def audit_experiment(output):
    audit(output / "scaling")
    meta = json.loads((output / "gate/metadata.json").read_text())
    certificate = json.loads((output / "gate/verification.json").read_text())
    assert meta["files"] == sources() and certificate["source_sha256"] == digest(sources())
    assert certificate["main_replications"] == 72 and certificate["sensitivity_replications"] == 4
    assert certificate["reused"] == 6 and sorted(certificate["reproduced_backends"]) == ["native", "production"]
    runs = json.loads((output / "gate/runs.json").read_text())
    assert len(runs) == 12
    for row in runs:
        prior.same_outputs(row["replications"])
    timings = [json.loads(x) for x in (output / "scaling/matrix.jsonl").read_text().splitlines()]
    rejected = []
    for field in ("backend_state", "fingerprint_digest", "kpis", "seed"):
        bad = copy.deepcopy(timings)
        bad[0]["replications"][0][field] = "corrupt"
        try:
            check_rows(bad)
        except AssertionError:
            rejected.append(field)
        else:
            raise AssertionError(f"accepted corrupt {field}")
    historical = []
    for folder in ("execution-results", "kpi-results"):
        for p in (ROOT / folder).rglob("*"):
            if p.is_file():
                assert p.read_bytes() == subprocess.check_output(
                    ["git", "show", f"e061ac1:{p.relative_to(REPO)}"], cwd=REPO
                )
                historical.append(str(p.relative_to(REPO)))
    result = dict(
        source_sha256=digest(sources()),
        main_gate_runs=72,
        sensitivity_runs=4,
        reproductions=2,
        timing_runs=72,
        rejected_corruptions=rejected,
        historical_evidence_files=len(historical),
        evidence={
            str(p.relative_to(output)): file_hash(p)
            for p in sorted(output.rglob("*"))
            if p.is_file() and p != output / "verification.json"
        },
    )
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "evidence"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    w = commands.add_parser("worker")
    w.add_argument("--project", type=Path, required=True)
    w.add_argument("--backend", choices=("native", "production"), required=True)
    w.add_argument("--producer", choices=("independent", "feedback"), required=True)
    w.add_argument("--jobs", type=int, required=True)
    for name in ("gate", "matrix", "audit", "audit-experiment"):
        commands.add_parser(name).add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(worker(args)))
    else:
        {"gate": gate, "matrix": matrix, "audit": audit, "audit-experiment": audit_experiment}[args.command](
            args.output
        )


if __name__ == "__main__":
    main()
