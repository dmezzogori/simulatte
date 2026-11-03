"""Ordinary managed API gate and cold/warm store timings for declared bundles."""

import argparse
import copy
from dataclasses import replace
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

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.execution.schema import _plain
from simulatte.execution.store import RunStore, _remove
from simulatte.experiments import run, Replications
from simulatte.stopping import Horizon
from . import backend_experiment as prior
from . import loading_experiment as previous
from .execution_project import create_project as base_project, ROOT, REPO, MODEL
from .scalar_benchmark import digest, libraries

MODELS = {"reference": MODEL, "managed": "prototypes/native_engine_dynamic/assets_model.py:build"}
SEED = 20261103


def create_project(target):
    project = base_project(target)
    shutil.copyfile(ROOT / "assets_model.py", project / "prototypes/native_engine_dynamic/assets_model.py")
    return project


def sources():
    extra = ("specs/research/2026-11-03-native-assets-integration-plan.md",
             "tests/execution/test_native_assets.py", "tests/execution/test_native_assets_integration.py",
             "docs/guides/experiments.md")
    return {**previous.sources(), **{name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in extra}}


def metadata():
    value = previous.metadata()
    value.update(files=sources(), source_sha256=digest(sources()), libraries=libraries(),
                 production_code_changed="declared native assets, verified context handles and owned storage",
                 native_C_changed=False, research_loader_configuration=False,
                 scope="ordinary managed API, local POSIX; existing scalar native production slice")
    return value


def definition(parameters, mode, *, count=3, stopping=Horizon(30000, warmup=500), seed=100):
    return replace(prior.prior.definition(parameters, count=count, stopping=stopping),
                   model=MODELS[mode], replications=Replications(count=count, base_seed=seed))


def read_results(project, run_id, mode, *, baseline=None):
    result = prior.read_results(project, run_id, baseline=baseline)
    with RunStore.open(project / ".simulatte") as store:
        for row, item in zip(result["replications"], store.replications(run_id)):
            committed = store.result(item["attempt"])
            row["artifact_sha256"] = hashlib.sha256(
                (committed.path / "artifacts/native-scalar-result.json").read_bytes()).hexdigest()
            row["attempt_id"] = item["attempt"]
            row["native_origin"] = committed.manifest["resolved"]["imports"].get("_nd_scalar")
            row["native_assets"] = _plain(committed.manifest["resolved"]["capabilities"].get("native_assets"))
            assert committed.manifest["request"]["model"] == MODELS[mode]
            if mode == "managed":
                bundle = row["native_assets"]["bundles"][0]
                assert bundle["name"] == "engine" and len(bundle["files"]) == 2
                entry = store.root.resolve() / "native-assets" / bundle["address"]
                if row["backend"] == "native":
                    extension = next(file["name"] for file in bundle["files"] if file["name"].startswith("_nd_scalar."))
                    assert row["native_origin"] == "external:" + str(entry / extension)
            else:
                assert row["native_assets"] is None
                if row["backend"] == "native":
                    assert row["native_origin"].startswith("snapshot:")
    return result


def comparable(rows):
    return [{key: r[key] for key in ("backend", "index", "seed", "kpis", "snapshot", "admitted", "steps", "proof",
                                    "initial_state_digest", "backend_state", "fingerprint_digest", "artifact_sha256")}
            for r in rows]


def worker(args):
    parameters = [dict(backend="native", producer=args.producer, jobs=args.jobs, verify=False)]
    started = time.perf_counter()
    summary = run(definition(parameters, args.mode), project=args.project, slots=SlotConfig(local=1), timeout=60)
    assert summary["counts"]["succeeded"] == 3 and summary["counts"].get("reused", 0) == 0
    result = read_results(args.project, summary["run_id"], args.mode)
    workflow_s = time.perf_counter() - started
    if args.mode == "managed":
        assert len({r["native_assets"]["bundles"][0]["address"] for r in result["replications"]}) == 1
    return dict(mode=args.mode, producer=args.producer, jobs=args.jobs, workflow_s=workflow_s, **result)


def warmup(args):
    parameters = [dict(backend="native", producer=args.producer, jobs=0, verify=False)]
    started = time.perf_counter()
    summary = run(definition(parameters, args.mode, count=1, seed=99, stopping=Horizon(1)),
                  project=args.project, slots=SlotConfig(local=1), timeout=60)
    assert summary["counts"]["succeeded"] == 1
    return dict(seconds=time.perf_counter() - started, run_id=summary["run_id"], seed=99, jobs=0)


def launch(project, mode, producer, jobs, *, warm=False):
    command = [sys.executable, "-m", "prototypes.native_engine_dynamic.assets_experiment",
               "worker", "--project", str(project), "--mode", mode, "--producer", producer, "--jobs", str(jobs)]
    prewarming = None
    if warm:
        prep = command.copy()
        prep[3] = "warmup"
        prewarming = json.loads(subprocess.check_output(prep, cwd=REPO, text=True,
                                                      env=dict(os.environ, PYTHONHASHSEED="0")))
    started = time.perf_counter()
    result = json.loads(subprocess.check_output(command, cwd=REPO, text=True,
                                               env=dict(os.environ, PYTHONHASHSEED="0")))
    result.update(process_s=time.perf_counter() - started, store="warm" if warm else "cold", warmup=prewarming)
    return result


def gate(output):
    output.mkdir(parents=True, exist_ok=False)
    meta, rows = metadata(), []
    with tempfile.TemporaryDirectory(prefix="declared-assets-gate-") as tmp:
        root = Path(tmp).resolve()
        template = create_project(root / "template")
        meta["binary_manifest"] = json.loads((template / "native-assets.json").read_text())
        projects = {mode: root / mode for mode in MODELS}
        for project in projects.values():
            shutil.copytree(template, project)
        for producer, policy, stopping in itertools.product(
            ("independent", "feedback"), ("fifo", "spt"),
            (Horizon(1), Horizon(10, warmup=2), Horizon(200, warmup=20)),
        ):
            observed = []
            for mode, project in projects.items():
                params = [dict(backend=b, producer=producer, policy=policy, jobs=40, servers=3, wip_cap=2)
                          for b in ("production", "native")]
                status = run(definition(params, mode, count=2, stopping=stopping), project=project,
                             slots=SlotConfig(local=2), timeout=60)
                result = read_results(project, status["run_id"], mode, baseline="production/base")
                prior.prior.same_outputs(result["replications"])
                observed.append(result)
            assert comparable(observed[0]["replications"]) == comparable(observed[1]["replications"])
            rows.append(dict(producer=producer, policy=policy, stopping=stopping.to_dict(),
                             reference=observed[0], managed=observed[1]))
            print(f"declared assets gate {len(rows)}/12 exact shared outputs, state and artifacts", flush=True)
        project = projects["managed"]
        selected = [r for r in rows[-1]["managed"]["replications"] if r["index"] == 0]
        spec = discover_project(project / MODELS["managed"].partition(":")[0], project)
        with Coordinator(spec) as coordinator:
            prepared = coordinator.prepare(timeout=60)
            request = coordinator.make_request(prepared, level="kpi")
            assert coordinator.preview(prepared, request, prepared.layout, timeout=60).reusable
        # Corrupt an idle cache entry; fresh reproduction must fail without repair.
        cache = project / ".simulatte/native-assets"
        entry = next(cache.iterdir())
        extension = next(entry.glob("_nd_scalar.*"))
        extension.chmod(0o644)
        with extension.open("ab") as file:
            file.write(b"corruption-probe")
        extension.chmod(0o444)
        with Coordinator.from_store(project, selected[0]["request_id"]) as coordinator:
            before = len(list(coordinator.store.root.joinpath("results").iterdir()))
            try:
                coordinator.reproduce(selected[0]["request_id"], timeout=60)
            except ExecutionFailure:
                pass
            else:
                raise AssertionError("corrupted native entry reproduced")
            assert len(list(coordinator.store.root.joinpath("results").iterdir())) == before
        with RunStore.open(project / ".simulatte") as store:
            pruned = store.prune()
            assert len(pruned["native_assets"]) == 1
        shutil.rmtree(project / "prototypes")
        reproduced = []
        with Coordinator.from_store(project, selected[0]["request_id"]) as coordinator:
            for original in selected:
                repeated = coordinator.reproduce(original["request_id"], timeout=60)
                assert repeated.fingerprint["digest"] == original["fingerprint_digest"]
                artifact = repeated.path / "artifacts/native-scalar-result.json"
                assert hashlib.sha256(artifact.read_bytes()).hexdigest() == original["artifact_sha256"]
                reproduced.append(original["backend"])
    assert sources() == meta["files"] and libraries() == meta["libraries"]
    for name, value in (("metadata", meta), ("runs", rows)):
        (output / (name + ".json")).write_text(json.dumps(value, separators=(",", ":")) + "\n")
    certificate = dict(source_sha256=meta["source_sha256"], main_runs=96, request_mode_pairs=48,
                       deleted_source_evicted_cache_reproductions=reproduced, ready_preview=True,
                       corrupted_entry_rejected=True, explicit_prune_rebuild=True,
                       ordinary_coordinator=True, native_C_unchanged=True)
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(certificate), flush=True)


def cases():
    return [dict(mode=mode, producer=producer, jobs=n, store=state, repeat=i)
            for mode, producer, n, state, i in itertools.product(
                MODELS, ("independent", "feedback"), (5000, 20000), ("cold", "warm"), range(3))]


def check_rows(rows):
    shared, identities = {}, {}
    attempts = []
    for row in rows:
        values = comparable(row["replications"])
        assert shared.setdefault((row["producer"], row["jobs"]), values) == values
        requests = [r["request_id"] for r in row["replications"]]
        key = row["mode"], row["producer"], row["jobs"], row["store"]
        assert identities.setdefault(key, requests) == requests
        attempts.extend(r["attempt_id"] for r in row["replications"])
        assert (row["warmup"] is not None) == (row["store"] == "warm")
        for result in row["replications"]:
            if row["mode"] == "managed":
                assert len(result["native_assets"]["bundles"]) == 1
                address = result["native_assets"]["bundles"][0]["address"]
                assert "/native-assets/" + address + "/" in result["native_origin"]
            else:
                assert result["native_assets"] is None and result["native_origin"].startswith("snapshot:")
    assert len(attempts) == len(set(attempts))


def matrix(output):
    output.mkdir(parents=True, exist_ok=False)
    meta, order, rows = metadata(), cases(), []
    random.Random(SEED).shuffle(order)
    meta.update(case_order=order, seed=SEED,
                timing="fresh ordinary API worker, three replications; warm-store preparatory run outside timing")
    with tempfile.TemporaryDirectory(prefix="declared-assets-matrix-") as tmp:
        root = Path(tmp)
        template = create_project(root / "template")
        meta["binary_manifest"] = json.loads((template / "native-assets.json").read_text())
        (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        for i, case in enumerate(order):
            project = root / str(i)
            shutil.copytree(template, project)
            row = launch(project, case["mode"], case["producer"], case["jobs"], warm=case["store"] == "warm")
            row["repeat"] = case["repeat"]
            rows.append(row)
            check_rows(rows)
            with (output / "matrix.jsonl").open("a") as file:
                file.write(json.dumps(row, separators=(",", ":")) + "\n")
            print(f"{i + 1}/48 {case} {row['process_s']:.3f}s", flush=True)
            _remove(project)
    audit(output)


def audit(output):
    meta = json.loads((output / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["libraries"] == libraries()
    rows = [json.loads(line) for line in (output / "matrix.jsonl").read_text().splitlines()]
    order = cases()
    random.Random(SEED).shuffle(order)
    assert len(rows) == 48 and meta["case_order"] == order
    for row, case in zip(rows, order):
        assert all(row[key] == value for key, value in case.items())
        assert row["process_s"] >= row["workflow_s"] > 0 and len(row["replications"]) == 3
    check_rows(rows)
    summary = []
    for producer, n, state in itertools.product(("independent", "feedback"), (5000, 20000), ("cold", "warm")):
        selected = [r for r in rows if (r["producer"], r["jobs"], r["store"]) == (producer, n, state)]
        values = {mode: [r["process_s"] for r in selected if r["mode"] == mode] for mode in MODELS}
        medians = {mode: statistics.median(samples) for mode, samples in values.items()}
        summary.append(dict(producer=producer, jobs=n, store=state, samples_s=values, medians_s=medians,
                            reduction=1 - medians["managed"] / medians["reference"]))
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    certificate = dict(source_sha256=meta["source_sha256"], experiments=48, replications=144,
                       untimed_preparatory_runs=24, exact_outcomes_fingerprints_artifacts=True,
                       ordinary_api_no_private_loader_configuration=True,
                       evidence={name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                                 for name in ("metadata.json", "matrix.jsonl", "summary.json")})
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(certificate), flush=True)


def audit_experiment(output):
    audit(output / "scaling")
    meta = json.loads((output / "gate/metadata.json").read_text())
    assert meta["files"] == sources()
    gate_rows = json.loads((output / "gate/runs.json").read_text())
    assert len(gate_rows) == 12
    for row in gate_rows:
        assert comparable(row["reference"]["replications"]) == comparable(row["managed"]["replications"])
    rows = [json.loads(line) for line in (output / "scaling/matrix.jsonl").read_text().splitlines()]
    rejected = []
    for field in ("fingerprint_digest", "artifact_sha256", "backend_state", "kpis", "native_origin", "native_assets"):
        bad = copy.deepcopy(rows)
        bad[0]["replications"][0][field] = "corrupt"
        try:
            check_rows(bad)
        except (AssertionError, TypeError):
            rejected.append(field)
        else:
            raise AssertionError("accepted corrupt " + field)
    count = 0
    for folder in ("kpi-results", "execution-results", "backend-results", "phase-results", "loading-results"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file():
                assert path.read_bytes() == subprocess.check_output(
                    ["git", "show", "1330e52:" + path.relative_to(REPO).as_posix()], cwd=REPO)
                count += 1
    for path in (ROOT / "csrc").glob("*.[ch]"):
        assert path.read_bytes() == subprocess.check_output(
            ["git", "show", "1330e52:" + path.relative_to(REPO).as_posix()], cwd=REPO)
    certificate = dict(source_sha256=digest(sources()), historical_evidence_files=count,
                       native_C_unchanged=True, rejected_corruptions=rejected,
                       evidence={path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sorted(output.rglob("*"))
                                 if path.is_file() and path != output / "verification.json"})
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps({k: v for k, v in certificate.items() if k != "evidence"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for action in ("worker", "warmup"):
        child = sub.add_parser(action)
        child.add_argument("--project", type=Path, required=True)
        child.add_argument("--mode", choices=MODELS, required=True)
        child.add_argument("--producer", choices=("independent", "feedback"), required=True)
        child.add_argument("--jobs", type=int, required=True)
    for action in ("gate", "matrix", "audit", "audit-experiment"):
        sub.add_parser(action).add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command in ("worker", "warmup"):
        print(json.dumps({"worker": worker, "warmup": warmup}[args.command](args), separators=(",", ":")))
    else:
        actions = {"gate": gate, "matrix": matrix, "audit": audit, "audit-experiment": audit_experiment}
        actions[args.command](args.output)


if __name__ == "__main__":
    main()
