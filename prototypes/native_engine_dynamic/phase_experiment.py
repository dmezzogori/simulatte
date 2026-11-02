"""Whole managed workflow phase accounting in disposable instrumented copies."""

import argparse
import copy
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

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.slots import SlotConfig
from simulatte.execution.store import RunStore
from simulatte.execution.snapshot import discover_project
from simulatte.stopping import Horizon
from . import backend_experiment as prior
from .execution_project import create_project, REPO, MODEL
from .phase_instrument import instrument
from .scalar_benchmark import digest, libraries

SEED = 20261102


def sources():
    return {**prior.sources(), "specs/research/2026-11-02-native-phase-profile-plan.md":
            hashlib.sha256((REPO / "specs/research/2026-11-02-native-phase-profile-plan.md").read_bytes()).hexdigest()}


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


class DiagnosticCoordinator(Coordinator):
    """Pass diagnostics outside request/source identity; use ordinary scheduling."""

    def __init__(self, *args, profile_output=None, **kwargs):
        self.profile_output = profile_output
        super().__init__(*args, **kwargs)

    def _send(self, worker, kind, body, *, key):
        if self.profile_output is not None and kind in ("run", "inspect"):
            body = {**body, "config": {**body["config"], "_phase_profile": dict(
                output=str(self.profile_output / (key.attempt_id + ".json")),
                label=kind + ":" + key.request_id,
            )}}
        return super()._send(worker, kind, body, key=key)


def validate_profiles(documents):
    for document in documents:
        assert document["schema"] == 1 and document["ended_ns"] >= document["started_ns"]
        for thread in document["threads"]:
            for totals in thread["totals"].values():
                assert totals["count"] > 0 and 0 <= totals["exclusive_ns"] <= totals["inclusive_ns"]
            for span in thread["spans"]:
                assert document["started_ns"] <= span["start_ns"] <= span["end_ns"] <= document["ended_ns"]
    return documents


def read_profiles(output):
    return validate_profiles([json.loads(p.read_text()) for p in sorted(output.glob("*.json"))])


def totals(documents, label=None):
    result = {}
    for document in documents:
        if label is not None and not document["label"].startswith(label):
            continue
        for thread in document["threads"]:
            for name, value in thread["totals"].items():
                row = result.setdefault(name, dict(count=0, inclusive_ns=0, exclusive_ns=0))
                for key in row:
                    row[key] += value[key]
    return result


def worker(args):
    # Instrumented workers import this module only after the disposable runtime
    # root has been prepended by launch(); regular source code is not reloaded.
    from simulatte import _phase_probe as probe

    enabled = args.mode == "active"
    profile_dir = args.profile_output
    profile_dir.mkdir(parents=True)
    if enabled:
        probe.configure(dict(output=str(profile_dir / "parent.json"), label="parent"))
    try:
        parameters = [dict(backend=b, producer=args.producer, jobs=args.jobs,
                           servers=3 if args.gate else 10, wip_cap=2 if args.gate else 12,
                           verify=args.gate) for b in ("production", "native")] if args.gate else [dict(
                               backend=args.backend, producer=args.producer, jobs=args.jobs, verify=False)]
        definition = prior.prior.definition(
            parameters, count=2 if args.gate else 3,
            stopping=Horizon(10, warmup=2) if args.gate else Horizon(30000, warmup=500),
        )
        spec = discover_project(args.project / MODEL.partition(":")[0], args.project)
        started = time.perf_counter()
        with DiagnosticCoordinator(spec, reference=MODEL, slots=SlotConfig(local=1),
                                   profile_output=profile_dir if enabled else None) as coordinator:
            run_id = coordinator.start_experiment(definition, timeout=60)
            summary = coordinator.wait_experiment(run_id)
            assert summary["status"] == "completed", summary
            if args.gate:
                for row in coordinator.store.replications(run_id)[:2]:
                    original = coordinator.store.result(row["attempt"])
                    reproduced = coordinator.reproduce(original.key.request_id, timeout=60)
                    assert reproduced.fingerprint == original.fingerprint
        observed = probe.timed("result.read_report")(
            prior.read_results
        )(args.project, run_id, baseline="production/base" if args.gate else None)
        if args.gate:
            prior.prior.same_outputs(observed["replications"])
        with RunStore.open(args.project / ".simulatte") as store:
            for row, stored in zip(observed["replications"], store.replications(run_id)):
                result = store.result(stored["attempt"])
                row["artifact_sha256"] = hashlib.sha256(
                    (result.path / "artifacts/native-scalar-result.json").read_bytes()
                ).hexdigest()
        elapsed = time.perf_counter() - started
    finally:
        if enabled:
            probe.finish()
    profiles = read_profiles(profile_dir)
    if enabled:
        count = 6 if args.gate else 3  # Four gate executions + two reproductions.
        assert len([p for p in profiles if p["label"].startswith("run:")]) == count
        assert len([p for p in profiles if p["label"].startswith("inspect:")]) == 1
        assert len(profiles) == count + 2
    else:
        assert profiles == []
    return dict(mode=args.mode, backend=args.backend, producer=args.producer, jobs=args.jobs,
                workflow_s=elapsed, profiles=profiles, **observed)


def launch(runtime, project, output, *, mode, backend="native", producer="independent", jobs=40, gate=False):
    command = [sys.executable, "-c", (
        "import sys;sys.path.insert(0," + repr(str(runtime)) + ");"
        "from prototypes.native_engine_dynamic.phase_experiment import main;main()"
    ), "worker", "--project", str(project), "--profile-output", str(output), "--mode", mode,
        "--backend", backend, "--producer", producer, "--jobs", str(jobs)]
    if gate:
        command.append("--gate")
    started_ns = time.perf_counter_ns()
    row = json.loads(subprocess.check_output(command, cwd=REPO, text=True, env=dict(os.environ, PYTHONHASHSEED="0")))
    ended_ns = time.perf_counter_ns()
    row.update(process_s=(ended_ns - started_ns) / 1e9, process_start_ns=started_ns, process_end_ns=ended_ns)
    # Local perf-counter timestamps must share the containing parent's epoch.
    for document in row["profiles"]:
        assert started_ns <= document["started_ns"] <= document["ended_ns"] <= ended_ns
    return row


def metadata():
    files = sources()
    return dict(created=datetime.now(UTC).isoformat(), files=files, source_sha256=digest(files),
                revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                libraries=libraries(), python=sys.version, clock=time.get_clock_info("perf_counter").implementation,
                scope="local CPython; same fixed native production slice; diagnostic disposable copies only")


def gate(output):
    output.mkdir(parents=True, exist_ok=False)
    meta = metadata()
    with tempfile.TemporaryDirectory(prefix="native-phase-gate-") as tmp:
        root = Path(tmp)
        project = create_project(root / "template")
        meta["instrumentation"] = instrument(root / "runtime", project)
        meta["runtime_files"] = tree_hashes(root / "runtime")
        meta["project_files"] = tree_hashes(project)
        rows = []
        for mode, producer in itertools.product(("dormant", "active"), ("independent", "feedback")):
            target = root / (mode + producer)
            shutil.copytree(project, target)
            row = launch(root / "runtime", target, root / (mode + producer + "-profiles"),
                         mode=mode, producer=producer, gate=True)
            rows.append(row)
            print(f"phase gate {mode} {producer} complete, ordinary reproduction passed", flush=True)
        for producer in ("independent", "feedback"):
            selected = [r for r in rows if r["producer"] == producer]
            for a, b in zip(selected[0]["replications"], selected[1]["replications"]):
                for key in ("request_id", "fingerprint_digest", "backend_state", "snapshot", "kpis", "proof", "steps",
                            "artifact_sha256"):
                    assert a[key] == b[key], key
        # Original source/runtime control: shared outcomes and backend state must
        # match. Ordinary fingerprint provenance changes with instrumented source.
        original = create_project(root / "original")
        definition = prior.prior.definition(
            [dict(backend=b, jobs=40, servers=3, wip_cap=2) for b in ("production", "native")],
            count=2, stopping=Horizon(10, warmup=2),
        )
        from simulatte.experiments import run
        status = run(definition, project=original, slots=SlotConfig(local=1), timeout=60)
        control = prior.read_results(original, status["run_id"], baseline="production/base")
        comparison = next(r for r in rows if r["mode"] == "dormant" and r["producer"] == "independent")
        for a, b in zip(control["replications"], comparison["replications"]):
            for key in ("backend_state", "snapshot", "kpis", "proof", "steps"):
                assert a[key] == b[key], key
    assert sources() == meta["files"] and libraries() == meta["libraries"]
    (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    (output / "runs.json").write_text(json.dumps(rows, separators=(",", ":")) + "\n")
    certificate = dict(source_sha256=meta["source_sha256"], main_runs=20, reproductions=8,
                       active_dormant_request_fingerprint_identity=True, original_shared_state_identity=True,
                       diagnostics_outside_results=True)
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(certificate), flush=True)


def cases():
    return [dict(backend=b, producer=p, jobs=n, mode=m, repeat=r)
            for b, p, n, m, r in itertools.product(("production", "native"), ("independent", "feedback"),
                                                  (5000, 20000), ("dormant", "active"), range(3))]


def check_rows(rows):
    groups, fingerprints = {}, {}
    for row in rows:
        values = [(r["seed"], r["kpis"], r["snapshot"], r["admitted"], r["steps"]) for r in row["replications"]]
        assert groups.setdefault((row["producer"], row["jobs"]), values) == values
        state = [(r["seed"], r["request_id"], r["fingerprint_digest"], r["backend_state"], r["artifact_sha256"])
                 for r in row["replications"]]
        assert fingerprints.setdefault((row["backend"], row["producer"], row["jobs"]), state) == state
        if row["mode"] == "active":
            validate_profiles(row["profiles"])
            assert len([p for p in row["profiles"] if p["label"].startswith("run:")]) == 3
            assert len(row["profiles"]) == 5
            aggregate = totals(row["profiles"], "run:")
            assert aggregate["backend.advance"]["count"] == sum(r["steps"] for r in row["replications"])
            assert aggregate["managed.python_step"]["count"] == aggregate["backend.advance"]["count"]
            assert aggregate["inputs.generate"]["count"] == 3
            assert aggregate["backend.prepare"]["count"] == 3
            assert aggregate["binding.finish"]["count"] == 3
        else:
            assert row["profiles"] == []


def matrix(output):
    output.mkdir(parents=True, exist_ok=False)
    meta, order, rows = metadata(), cases(), []
    random.Random(SEED).shuffle(order)
    meta.update(
        seed=SEED, case_order=order,
        timing="whole fresh-process experiment, three replications; active/dormant disposable instrumented copies",
    )
    with tempfile.TemporaryDirectory(prefix="native-phase-timing-") as tmp:
        root = Path(tmp)
        template = create_project(root / "template")
        meta["instrumentation"] = instrument(root / "runtime", template)
        meta["runtime_files"] = tree_hashes(root / "runtime")
        meta["project_files"] = tree_hashes(template)
        (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        for index, case in enumerate(order):
            project = root / str(index)
            shutil.copytree(template, project)
            row = launch(root / "runtime", project, root / (str(index) + "-profiles"),
                         **{k: v for k, v in case.items() if k != "repeat"})
            row["repeat"] = case["repeat"]
            rows.append(row)
            check_rows(rows)
            with (output / "matrix.jsonl").open("a") as file:
                file.write(json.dumps(row, separators=(",", ":")) + "\n")
            print(f"{index + 1}/48 {case} {row['process_s']:.3f}s", flush=True)
            shutil.rmtree(project)
    audit(output)


def audit(output):
    meta = json.loads((output / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["source_sha256"] == digest(sources())
    assert meta["libraries"] == libraries()
    rows = [json.loads(line) for line in (output / "matrix.jsonl").read_text().splitlines()]
    assert len(rows) == 48
    order = cases()
    random.Random(SEED).shuffle(order)
    assert meta["case_order"] == order
    for row, case in zip(rows, order):
        assert all(row[key] == value for key, value in case.items())
        assert len(row["replications"]) == 3 and row["process_s"] >= row["workflow_s"] > 0
    check_rows(rows)
    summary = []
    for backend, producer, n in itertools.product(("production", "native"), ("independent", "feedback"), (5000, 20000)):
        selected = [r for r in rows if (r["backend"], r["producer"], r["jobs"]) == (backend, producer, n)]
        wall = {m: statistics.median(r["process_s"] for r in selected if r["mode"] == m) for m in ("dormant", "active")}
        active = [r for r in selected if r["mode"] == "active"]
        aggregate = [totals(r["profiles"]) for r in active]
        names = set.intersection(*(set(a) for a in aggregate))
        phases = {name: dict(inclusive_s=statistics.median(a[name]["inclusive_ns"] / 1e9 for a in aggregate),
                             exclusive_s=statistics.median(a[name]["exclusive_ns"] / 1e9 for a in aggregate))
                  for name in sorted(names)}
        summary.append(dict(backend=backend, producer=producer, jobs=n, wall_s=wall,
                            overhead_fraction=wall["active"] / wall["dormant"] - 1, phases=phases))
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    certificate = dict(source_sha256=meta["source_sha256"], experiments=48, replications=144,
                       exact_shared_results=True, active_dormant_fingerprints_equal=True,
                       evidence={p: hashlib.sha256((output / p).read_bytes()).hexdigest()
                                 for p in ("metadata.json", "matrix.jsonl", "summary.json")})
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(certificate), flush=True)


def audit_experiment(output):
    audit(output / "scaling")
    gate_meta = json.loads((output / "gate/metadata.json").read_text())
    assert gate_meta["files"] == sources()
    gate_rows = json.loads((output / "gate/runs.json").read_text())
    for producer in ("independent", "feedback"):
        dormant, active = [r for r in gate_rows if r["producer"] == producer]
        assert dormant["mode"] == "dormant" and active["mode"] == "active"
        validate_profiles(active["profiles"])
        for a, b in zip(dormant["replications"], active["replications"]):
            for key in ("request_id", "fingerprint_digest", "backend_state", "artifact_sha256", "proof", "kpis"):
                assert a[key] == b[key], key
    rows = [json.loads(line) for line in (output / "scaling/matrix.jsonl").read_text().splitlines()]
    rejected = []
    for field in ("backend_state", "fingerprint_digest", "artifact_sha256", "kpis", "seed", "profile"):
        corrupt = copy.deepcopy(rows)
        if field == "profile":
            row = next(r for r in corrupt if r["mode"] == "active")
            document = row["profiles"][0]
            aggregate = next(iter(document["threads"][0]["totals"].values()))
            aggregate["exclusive_ns"] = aggregate["inclusive_ns"] + 1
        else:
            corrupt[0]["replications"][0][field] = "corrupt"
        try:
            check_rows(corrupt)
        except AssertionError:
            rejected.append(field)
        else:
            raise AssertionError("accepted corrupted " + field)
    historical = 0
    for folder in ("execution-results", "kpi-results", "backend-results"):
        for path in (REPO / "prototypes/native_engine_dynamic" / folder).rglob("*"):
            if path.is_file():
                assert path.read_bytes() == subprocess.check_output(
                    ["git", "show", "fee446b:" + path.relative_to(REPO).as_posix()], cwd=REPO)
                historical += 1
    # Production implementation and native transition/binding sources stay exact.
    production = list((REPO / "src/simulatte").rglob("*.py"))
    native = list((REPO / "prototypes/native_engine_dynamic/csrc").glob("*.[ch]"))
    for path in production + native:
        assert path.read_bytes() == subprocess.check_output(
            ["git", "show", "fee446b:" + path.relative_to(REPO).as_posix()], cwd=REPO)
    certificate = dict(source_sha256=digest(sources()), historical_evidence_files=historical,
                       production_and_native_sources_unchanged=True, rejected_corruptions=rejected,
                       evidence={p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted(output.rglob("*"))
                                 if p.is_file() and p != output / "verification.json"})
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps({k: v for k, v in certificate.items() if k != "evidence"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    child = sub.add_parser("worker")
    child.add_argument("--project", type=Path, required=True)
    child.add_argument("--profile-output", type=Path, required=True)
    child.add_argument("--mode", choices=("active", "dormant"), required=True)
    child.add_argument("--backend", choices=("native", "production"), required=True)
    child.add_argument("--producer", choices=("independent", "feedback"), required=True)
    child.add_argument("--jobs", type=int, required=True)
    child.add_argument("--gate", action="store_true")
    for name in ("gate", "matrix", "audit", "audit-experiment"):
        sub.add_parser(name).add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(worker(args), separators=(",", ":")))
    else:
        actions = {"gate": gate, "matrix": matrix, "audit": audit, "audit-experiment": audit_experiment}
        actions[args.command](args.output)


if __name__ == "__main__":
    main()
