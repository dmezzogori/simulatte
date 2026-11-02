"""Real managed replications, scalar reports and fresh-process execution costs."""

import argparse
import copy
from dataclasses import replace
from datetime import datetime, UTC
import itertools
import json
import os
from pathlib import Path
import platform
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
from simulatte.execution.schema import content_id
from simulatte.execution.snapshot import discover_project
from simulatte.execution.store import RunStore, _read
from simulatte.experiments import Experiment, Explicit, Replications, run
from simulatte.experiments.results import ExperimentReport
from simulatte.stopping import Horizon, FinitePopulation
from prototypes.native_engine.benchmark import file_hash
from .execution_project import ROOT, REPO, MODEL, create_project
from .kpi_experiment import sources as prior_sources
from .scalar_benchmark import digest

SEED = 20261102


def sources():
    return {
        **prior_sources(),
        str((REPO / "specs/research/2026-11-02-native-execution-plan.md").relative_to(REPO)): file_hash(
            REPO / "specs/research/2026-11-02-native-execution-plan.md"
        ),
    }


def metadata():
    files = sources()
    return dict(
        created=datetime.now(UTC).isoformat(),
        source_sha256=digest(files),
        files=files,
        python=sys.version,
        platform=platform.platform(),
        revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        scope="local CPython; fixed unit servers, FIFO/SPT, EDD ConWIP; Horizon; KPI result file only",
        kernel_changed=False,
        production_code_changed=False,
    )


def definition(
    parameters, *, name="native scalar execution", stopping=Horizon(30000, warmup=500), count=3, level="kpi"
):
    return Experiment(
        name=name,
        model=MODEL,
        replications=Replications(count=count, base_seed=100),
        configurations=Explicit([dict(label=p["backend"], parameters=p) for p in parameters]),
        stopping=stopping,
        level=level,
    )


def read_results(project, run_id, *, baseline=None):
    with RunStore.open(project / ".simulatte") as store:
        started = time.perf_counter()
        report = ExperimentReport(store, run_id).to_dict(baseline=baseline)
        statistics_s = time.perf_counter() - started
        assert report["status"] == "completed" and report["missing_total"] == 0, report
        rows = []
        for row in store.replications(run_id):
            assert row["status"] in ("succeeded", "reused")
            result = store.result(row["attempt"])
            document = json.loads((result.path / "artifacts/native-scalar-result.json").read_text())
            kpis = dict(result.fingerprint["kpis"])
            trace = Trace.open(result.path / "run.simtrace")
            trace.check()
            assert trace.level == "kpi" and trace.outcome == "completed" and not trace.truncated
            assert trace.verify() == "not_verifiable" and trace.kpi_series() == {} and not list(trace.events())
            assert trace.kpis() == kpis == document["snapshot"]["scalars"]
            requested = result.manifest["request"]
            assert requested["seed"] == str(100 + row["index"])
            assert requested["level"] == "kpi"
            imports = result.manifest["resolved"]["imports"]
            assert imports["prototypes.native_engine_dynamic.execution_model"].startswith("snapshot:")
            rows.append(
                dict(
                    configuration=row["configuration"],
                    index=row["index"],
                    seed=100 + row["index"],
                    request_id=result.key.request_id,
                    content_id=content_id(store.request(result.key.request_id)),
                    trace_sha256=file_hash(result.path / "run.simtrace"),
                    trace_bytes=(result.path / "run.simtrace").stat().st_size,
                    times={"managed_run_s": float(trace._footer["volatile"]["run_seconds"])},
                    kpis=kpis,
                    **document,
                )
            )
        paired = report.get("paired", [])
        for item in paired:
            if item["n"]:
                assert item["estimate"]["mean"] == 0
                if item["n"] >= 2:
                    assert item["estimate"]["half_width"] == 0
        return dict(run_id=run_id, replications=rows, report=report, statistics_s=statistics_s)


def same_outputs(rows):
    groups = {}
    for row in rows:
        observed = {k: row[k] for k in ("snapshot", "kpis", "admitted", "steps", "proof")}
        assert groups.setdefault(row["index"], observed) == observed, row["index"]


def gate(output):
    output.mkdir(parents=True, exist_ok=False)
    meta = metadata()
    rows = []
    with tempfile.TemporaryDirectory(prefix="native-execution-gate-") as tmp:
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
            experiment = definition(params, stopping=stopping)
            summary = run(experiment, project=project, slots=SlotConfig(local=2), timeout=30)
            observed = read_results(project, summary["run_id"], baseline="production/base")
            same_outputs(observed["replications"])
            assert len(observed["replications"]) == 6
            rows.append(dict(producer=producer, policy=policy, stopping=stopping.to_dict(), **observed))
            print(f"gate {len(rows)}/12 exact paired replications", flush=True)
        # Completed requests must be reused, not executed again.
        repeated = run(experiment, project=project, slots=SlotConfig(local=2), timeout=30)
        assert repeated["run_id"] == rows[-1]["run_id"]
        reused_definition = replace(experiment, name="native scalar execution reuse")
        reused = run(reused_definition, project=project, slots=SlotConfig(local=2), timeout=30)
        assert reused["counts"]["reused"] == 6
        reused_result = read_results(project, reused["run_id"], baseline="production/base")
        assert [r["content_id"] for r in reused_result["replications"]] == [
            r["content_id"] for r in rows[-1]["replications"]
        ]
        reproduced = []
        spec = discover_project(project / MODEL.partition(":")[0], project)
        with Coordinator(spec, reference=MODEL) as coordinator:
            for original in rows[-1]["replications"][:2]:
                repeated_result = coordinator.reproduce(original["request_id"], timeout=30)
                assert dict(repeated_result.fingerprint["kpis"]) == original["kpis"]
                reproduced.append(original["backend"])
        assert sorted(reproduced) == ["native", "production"]
        negatives = []
        for feature in ("full", "population", "unknown_kpi"):
            bad = definition([dict(backend="native", jobs=2)], count=1)
            if feature == "full":
                bad = replace(bad, level="full")
            elif feature == "population":
                bad = replace(bad, stopping=FinitePopulation(arrivals=2, safety_limit=10))
            else:
                bad = replace(bad, kpis=("shopfloor/unknown",))
            failed = run(bad, project=project, slots=SlotConfig(local=1), timeout=30)
            assert failed["status"] == "failed" and failed["counts"]["failed"] == 1
            with RunStore.open(project / ".simulatte") as store:
                item = store.replications(failed["run_id"])[0]
                with store._transaction() as db:
                    attempt = db.execute(
                        "SELECT status,details FROM attempts WHERE id=?", (item["attempt"],)
                    ).fetchone()
                    assert (
                        db.execute("SELECT COUNT(*) FROM results WHERE attempt=?", (item["attempt"],)).fetchone()[0]
                        == 0
                    )
                details = _read(attempt["details"])
                message = details.get("message", "")
                assert "unsupported" in message or "Unknown requested KPI" in message, message
                negatives.append(dict(feature=feature, status=attempt["status"], message=message))
        # Snapshot identity includes the native binary bytes.
        with Coordinator(discover_project(project / MODEL.partition(":")[0], project), reference=MODEL) as coordinator:
            before = coordinator.prepare(timeout=30).snapshot.source.content_hash
        asset = project / next(
            p for p in meta["native_assets"]["files"] if p.endswith(".dylib") or p.endswith("dynamic_engine.so")
        )
        with asset.open("ab") as target:
            target.write(b"corruption-probe")
        with Coordinator(discover_project(project / MODEL.partition(":")[0], project), reference=MODEL) as coordinator:
            after = coordinator.prepare(timeout=30).snapshot.source.content_hash
        assert before != after
        failed = run(
            definition([dict(backend="native", jobs=2)], count=1, name="asset corruption"),
            project=project,
            slots=SlotConfig(local=1),
            timeout=30,
        )
        assert failed["counts"]["failed"] == 1
    assert meta["files"] == sources()
    (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    (output / "runs.json").write_text(json.dumps(rows, indent=2) + "\n")
    certificate = dict(
        experiments=len(rows),
        successful_replications=72,
        exact_outputs=True,
        request_reuse=True,
        content_reuse=6,
        reproduced_backends=reproduced,
        rejected=negatives,
        native_asset_changes_source_identity=True,
        corrupted_asset_rejected=True,
        source_sha256=meta["source_sha256"],
    )
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(certificate), flush=True)


def worker(args):
    experiment = definition([dict(backend=args.backend, producer=args.producer, jobs=args.jobs, verify=False)])
    started = time.perf_counter()
    summary = run(experiment, project=args.project, slots=SlotConfig(local=1), timeout=60)
    execution_s = time.perf_counter() - started
    assert summary["status"] == "completed" and summary["counts"]["succeeded"] == 3
    started = time.perf_counter()
    observed = read_results(args.project, summary["run_id"])
    validation_reporting_s = time.perf_counter() - started
    assert all(
        len(r["kpis"]) == 8 and r["admitted"] == args.jobs and r["proof"] is None for r in observed["replications"]
    )
    return dict(
        backend=args.backend,
        producer=args.producer,
        jobs=args.jobs,
        execution_s=execution_s,
        validation_reporting_s=validation_reporting_s,
        **observed,
    )


def cases():
    return [
        dict(backend=b, producer=p, jobs=n, repeat=r)
        for b, p, n, r in itertools.product(
            ("production", "native"), ("independent", "feedback"), (5000, 20000), range(3)
        )
    ]


def check_matrix(rows):
    groups = {}
    for row in rows:
        values = [(r["seed"], r["kpis"], r["snapshot"], r["admitted"], r["steps"]) for r in row["replications"]]
        key = row["producer"], row["jobs"]
        assert groups.setdefault(key, values) == values, key


def matrix(output):
    output.mkdir(parents=True, exist_ok=False)
    meta, order, rows = metadata(), cases(), []
    random.Random(SEED).shuffle(order)
    meta.update(
        case_order=order,
        seed=SEED,
        timing=(
            "fresh worker process + imports + real API preparation/execution + stored result "
            "validation/statistics + coordinator cleanup; native build/project copy excluded"
        ),
    )
    with tempfile.TemporaryDirectory(prefix="native-execution-timings-") as tmp:
        template = create_project(Path(tmp) / "template")
        meta["native_assets"] = json.loads((template / "native-assets.json").read_text())
        (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        for i, case in enumerate(order):
            project = Path(tmp) / f"case-{i}"
            shutil.copytree(template, project)
            command = [
                sys.executable,
                "-m",
                "prototypes.native_engine_dynamic.execution_experiment",
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
            check_matrix(rows)
            with (output / "matrix.jsonl").open("a") as target:
                target.write(json.dumps(row) + "\n")
            print(f"{i + 1}/24 {case} {row['process_s']:.3f}s", flush=True)
            shutil.rmtree(project)
    audit(output)


def audit(output):
    meta = json.loads((output / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["source_sha256"] == digest(sources())
    order = cases()
    random.Random(SEED).shuffle(order)
    assert meta["case_order"] == order
    rows = [json.loads(line) for line in (output / "matrix.jsonl").read_text().splitlines()]
    assert len(rows) == len(order)
    for row, case in zip(rows, order):
        assert all(row[k] == value for k, value in case.items())
        assert row["process_s"] >= row["execution_s"] + row["validation_reporting_s"] > 0
        assert len(row["replications"]) == 3
        assert all(
            len(r["kpis"]) == 8 and r["proof"] is None and r["admitted"] == row["jobs"] for r in row["replications"]
        )
    check_matrix(rows)
    summary = []
    for producer, jobs in itertools.product(("independent", "feedback"), (5000, 20000)):
        selected = [r for r in rows if r["producer"] == producer and r["jobs"] == jobs]
        values = {
            backend: [r["process_s"] for r in selected if r["backend"] == backend]
            for backend in ("production", "native")
        }
        summary.append(
            dict(
                producer=producer,
                jobs=jobs,
                replications_per_experiment=3,
                production_s=statistics.median(values["production"]),
                native_s=statistics.median(values["native"]),
                speedup=statistics.median(values["production"]) / statistics.median(values["native"]),
            )
        )
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    cert = dict(
        source_sha256=meta["source_sha256"],
        experiments=len(rows),
        replications=3 * len(rows),
        exact_outputs=True,
        evidence={name: file_hash(output / name) for name in ("metadata.json", "matrix.jsonl", "summary.json")},
    )
    (output / "verification.json").write_text(json.dumps(cert, indent=2) + "\n")
    print(json.dumps(cert), flush=True)


def audit_experiment(output):
    audit(output / "scaling")
    meta = json.loads((output / "gate/metadata.json").read_text())
    cert = json.loads((output / "gate/verification.json").read_text())
    assert meta["files"] == sources() and meta["source_sha256"] == digest(sources()) == cert["source_sha256"]
    assert cert["successful_replications"] == 72 and cert["content_reuse"] == 6 and cert["corrupted_asset_rejected"]
    assert sorted(cert["reproduced_backends"]) == ["native", "production"]
    rows = json.loads((output / "gate/runs.json").read_text())
    assert len(rows) == 12
    for row in rows:
        same_outputs(row["replications"])
    timings = [json.loads(line) for line in (output / "scaling/matrix.jsonl").read_text().splitlines()]
    rejected = []
    for field in ("kpis", "snapshot", "admitted", "seed"):
        bad = copy.deepcopy(timings)
        bad[0]["replications"][0][field] = "corrupt"
        try:
            check_matrix(bad)
        except AssertionError:
            rejected.append(field)
        else:
            raise AssertionError(f"accepted corrupted {field}")
    # Prior native KPI results and production code remain the committed control.
    verified = 0
    paths = [*(REPO / "src/simulatte").rglob("*.py"), *(ROOT / "kpi-results").rglob("*")]
    for path in paths:
        if path.is_file():
            assert path.read_bytes() == subprocess.check_output(
                ["git", "show", f"c379217:{path.relative_to(REPO)}"], cwd=REPO
            )
            verified += 1
    result = dict(
        source_sha256=digest(sources()),
        gate_replications=72,
        timing_replications=72,
        historical_files=verified,
        rejected_corruptions=rejected,
        production_and_c_engine_unchanged=True,
        evidence={
            str(p.relative_to(output)): file_hash(p)
            for p in sorted(output.rglob("*"))
            if p.is_file() and p != output / "verification.json"
        },
    )
    for path in (ROOT / "csrc").glob("*.[ch]"):
        assert path.read_bytes() == subprocess.check_output(
            ["git", "show", f"c379217:{path.relative_to(REPO)}"], cwd=REPO
        )
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "evidence"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    worker_parser = commands.add_parser("worker")
    worker_parser.add_argument("--project", type=Path, required=True)
    worker_parser.add_argument("--backend", choices=("native", "production"), required=True)
    worker_parser.add_argument("--producer", choices=("independent", "feedback"), required=True)
    worker_parser.add_argument("--jobs", type=int, required=True)
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
