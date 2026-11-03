"""Verified immutable binary-path experiment; original runtime/C stay unchanged."""

import argparse
import ast
import copy
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

from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.execution.store import RunStore, _read
from simulatte.stopping import Horizon
from . import phase_experiment as prior
from .execution_project import create_project, ROOT, REPO, MODEL
from .immutable_assets import publish, descriptor, address, verify_entry, STORE_ENV, DIAGNOSTIC_ENV
from .phase_instrument import instrument, transform
from .scalar_benchmark import digest, libraries

SEED = 20261103


def sources():
    name = "specs/research/2026-11-03-native-immutable-loading-plan.md"
    return {**prior.sources(), name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()}


def prepare(runtime, project, store):
    changed = instrument(runtime, project)
    module = project / "prototypes/native_engine_dynamic"
    shutil.copyfile(ROOT / "immutable_assets.py", module / "immutable_assets.py")
    helper = module / "immutable_assets.py"
    helper.write_text(transform(helper.read_text(), [
        ("load_verified", "native.verified_load", True),
        ("_import_extension", "native.binary_import", True),
        ("verify_pair", "native.pair_verify", True),
    ]))
    compiled = module / "compiled.py"
    tree = ast.parse(compiled.read_text())
    matched = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "extension":
            node.body = ast.parse("from .immutable_assets import load_verified\nreturn load_verified(EXTENSION)").body
            matched += 1
    assert matched == 1
    compiled.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n")
    bootstrap = runtime / "simulatte/execution/bootstrap.py"
    code = bootstrap.read_text()
    marker = "        _phase_configure(config.get('_phase_profile'))"
    assert code.count(marker) == 1
    additions = "\n".join((marker,
        "        settings = config.get('_immutable_native_assets')",
        "        if settings is not None:",
        "            if settings['store'] is None:",
        "                os.environ.pop('" + STORE_ENV + "', None)",
        "            else:",
        "                os.environ['" + STORE_ENV + "'] = settings['store']",
        "            os.environ['" + DIAGNOSTIC_ENV + "'] = settings['diagnostic']",
    ))
    code = code.replace(marker, additions)
    compile(code, str(bootstrap), "exec")
    bootstrap.write_text(code)
    extension = next((module / ".build").glob("_nd_scalar.*"))
    started = time.perf_counter()
    entry = publish(extension, store)
    setup_s = time.perf_counter() - started
    verify_entry(entry, descriptor(extension))
    return dict(targets=changed, runtime_files=prior.tree_hashes(runtime), project_files=prior.tree_hashes(project),
                address=entry.name, files=descriptor(extension)["files"], publish_s=setup_s,
                entry=str(entry), native_preloaded=False)


class LoadingCoordinator(prior.DiagnosticCoordinator):
    def __init__(self, *args, loading, asset_store, loader_output, cancel_ready=False, **kwargs):
        self.loading, self.asset_store = loading, asset_store
        self.loader_output, self.cancel_ready = loader_output, cancel_ready
        super().__init__(*args, **kwargs)
        extension = next((self.project.root / "prototypes/native_engine_dynamic/.build").glob("_nd_scalar.*"))
        self.asset_entry = self.asset_store / address(descriptor(extension))

    def _send(self, worker, kind, body, *, key):
        if kind == "ready_ack" and self.cancel_ready:
            assert self.cancel(key)
            return None
        if kind == "run":
            configuration = body["config"]
            if self.loading == "immutable":
                # Keep ordinary origin enforcement and record the true native
                # path. Only this addressed pair directory is an external root.
                configuration = {**configuration,
                                 "external_roots": [*configuration["external_roots"], str(self.asset_entry)]}
            body = {**body, "config": {**configuration, "_immutable_native_assets": dict(
                store=None if self.loading == "attempt" else str(self.asset_store),
                diagnostic=str(self.loader_output / (key.attempt_id + ".json")),
            )}}
        return super()._send(worker, kind, body, key=key)


def worker(args):
    from simulatte import _phase_probe as probe

    enabled = args.probe == "active"
    profile_dir, loader_dir = args.output / "phases", args.output / "loaders"
    profile_dir.mkdir(parents=True)
    loader_dir.mkdir()
    if enabled:
        probe.configure(dict(output=str(profile_dir / "parent.json"), label="parent"))
    special = args.cancel_ready or args.expect_failure
    try:
        if args.gate:
            parameters = [dict(backend=b, producer=args.producer, policy=args.policy,
                               jobs=40, servers=3, wip_cap=2) for b in ("production", "native")]
        else:
            parameters = [dict(backend="native", producer=args.producer, jobs=args.jobs, verify=False)]
        definition = prior.prior.prior.definition(
            parameters, count=1 if special else 2 if args.gate else 3,
            stopping=Horizon(10, warmup=2) if args.gate or special else Horizon(30000, warmup=500),
        )
        spec = discover_project(args.project / MODEL.partition(":")[0], args.project)
        started = time.perf_counter()
        with LoadingCoordinator(spec, reference=MODEL, slots=SlotConfig(local=1), loading=args.loading,
                                asset_store=args.asset_store, loader_output=loader_dir,
                                cancel_ready=args.cancel_ready,
                                profile_output=profile_dir if enabled else None) as coordinator:
            run_id = coordinator.start_experiment(definition, timeout=60)
            status = coordinator.wait_experiment(run_id)
            if special:
                count = "cancelled" if args.cancel_ready else "failed"
                assert status["counts"][count] == 1, status
                attempt = coordinator.store.replications(run_id)[0]["attempt"]
                with coordinator.store._transaction() as db:
                    assert db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
                    details = _read(db.execute("SELECT details FROM attempts WHERE id=?", (attempt,)).fetchone()[0])
                observed = dict(status=status, details=details)
            else:
                assert status["counts"]["succeeded"] == (4 if args.gate else 3), status
                if args.gate:
                    # Reproduce each backend with the other native loading path.
                    coordinator.loading = "immutable" if args.loading == "attempt" else "attempt"
                    for item in coordinator.store.replications(run_id)[:2]:
                        original = coordinator.store.result(item["attempt"])
                        repeated = coordinator.reproduce(original.key.request_id, timeout=60)
                        assert repeated.fingerprint == original.fingerprint
        if not special:
            observed = probe.timed("result.read_report")(prior.prior.read_results)(
                args.project, run_id, baseline="production/base" if args.gate else None)
            if args.gate:
                prior.prior.prior.same_outputs(observed["replications"])
            with RunStore.open(args.project / ".simulatte") as store:
                for row, item in zip(observed["replications"], store.replications(run_id)):
                    result = store.result(item["attempt"])
                    row["artifact_sha256"] = hashlib.sha256(
                        (result.path / "artifacts/native-scalar-result.json").read_bytes()).hexdigest()
                    row["attempt_id"] = item["attempt"]
                    row["native_origin"] = result.manifest["resolved"]["imports"].get("_nd_scalar")
        workflow_s = time.perf_counter() - started
    finally:
        if enabled:
            probe.finish()
    profiles = prior.read_profiles(profile_dir)
    loaders = [dict(attempt_id=p.stem, **json.loads(p.read_text())) for p in sorted(loader_dir.glob("*.json"))]
    if special:
        aggregate = prior.totals(profiles, "run:")
        assert aggregate.get("backend.advance", {}).get("count", 0) == 0
        if args.expect_failure:
            assert loaders == [] and aggregate.get("native.binary_import", {}).get("count", 0) == 0
        else:
            assert len(loaders) == 1
    else:
        assert len(loaders) == 3
        assert len({p["pid"] for p in loaders}) == 3
        by_attempt = {item["attempt_id"]: item for item in loaders}
        for row in observed["replications"]:
            if row["backend"] == "native":
                loaded = by_attempt[row["attempt_id"]]
                origin = ("snapshot:prototypes/native_engine_dynamic/.build/" + Path(loaded["extension"]).name
                          if loaded["mode"] == "attempt" else "external:" + loaded["extension"])
                assert row["native_origin"] == origin
    return dict(loading=args.loading, probe=args.probe, producer=args.producer, policy=args.policy,
                jobs=args.jobs, workflow_s=workflow_s, loaders=loaders, profiles=profiles, **observed)


def launch(runtime, project, output, store, *, loading, probe="dormant", producer="independent", jobs=40,
           policy="spt", gate=False, cancel_ready=False, expect_failure=False):
    command = [sys.executable, "-c", "import sys;sys.path.insert(0," + repr(str(runtime)) + ");"
               "from prototypes.native_engine_dynamic.loading_experiment import main;main()",
               "worker", "--project", str(project), "--output", str(output), "--asset-store", str(store),
               "--loading", loading, "--probe", probe, "--producer", producer, "--jobs", str(jobs),
               "--policy", policy]
    for flag, selected in (("--gate", gate), ("--cancel-ready", cancel_ready), ("--expect-failure", expect_failure)):
        if selected:
            command.append(flag)
    started_ns = time.perf_counter_ns()
    row = json.loads(subprocess.check_output(command, cwd=REPO, text=True, env=dict(os.environ, PYTHONHASHSEED="0")))
    ended_ns = time.perf_counter_ns()
    row.update(process_s=(ended_ns - started_ns) / 1e9)
    for document in row["profiles"]:
        assert started_ns <= document["started_ns"] <= document["ended_ns"] <= ended_ns
    return row


def metadata():
    value = prior.metadata()
    value.update(files=sources(), source_sha256=digest(sources()), libraries=libraries(),
                 scope="local CPython; verified immutable binary paths vs per-attempt paths; fresh children")
    return value


def comparable(row):
    return [{key: r[key] for key in ("seed", "backend_state", "fingerprint_digest", "kpis", "snapshot", "proof",
                                    "steps", "artifact_sha256", "request_id")} for r in row["replications"]]


def verify_loaders(row, fixture, *, gate=False):
    assert len(row["loaders"]) == 3 and len({d["pid"] for d in row["loaders"]}) == 3
    for item in row["loaders"]:
        assert item["files"] == fixture["files"] and item["address"] == fixture["address"]
        assert item["extension"].startswith(item["selected"] + "/")
        assert all(path.startswith(item["selected"] + "/") for path in item["images"])
        assert 0 <= item["import_s"] <= item["total_s"]
        assert abs(item["total_s"] - sum(item[k] for k in ("verification_s", "import_s", "postcheck_s"))) < 1e-8
        if not gate:
            assert item["mode"] == row["loading"]
        assert (item["selected"] == item["source"]) == (item["mode"] == "attempt")
        if item["mode"] == "immutable":
            assert item["selected"] == fixture["entry"]


def gate(output):
    output.mkdir(parents=True, exist_ok=False)
    meta, rows = metadata(), []
    with tempfile.TemporaryDirectory(prefix="immutable-loading-gate-") as tmp:
        root = Path(tmp).resolve()
        template = create_project(root / "template")
        fixture = prepare(root / "runtime", template, root / "assets")
        meta["fixture"] = fixture
        for loading, producer, policy in itertools.product(("attempt", "immutable"),
                                                           ("independent", "feedback"), ("fifo", "spt")):
            name = f"{loading}-{producer}-{policy}"
            project = root / name
            shutil.copytree(template, project)
            row = launch(root / "runtime", project, root / (name + "-diagnostics"), root / "assets",
                         loading=loading, producer=producer, policy=policy, probe="active", gate=True)
            verify_loaders(row, fixture, gate=True)
            rows.append(row)
            print(f"loading gate {name}, cross-path reproduction passed", flush=True)
        for producer, policy in itertools.product(("independent", "feedback"), ("fifo", "spt")):
            a, b = [r for r in rows if r["producer"] == producer and r["policy"] == policy]
            assert comparable(a) == comparable(b)
        negatives = []
        for loading in ("attempt", "immutable"):
            project = root / ("cancel-" + loading)
            shutil.copytree(template, project)
            row = launch(root / "runtime", project, root / ("cancel-" + loading + "-diagnostics"), root / "assets",
                         loading=loading, probe="active", cancel_ready=True)
            negatives.append(dict(case="ready-cancel-" + loading, **row))
        for kind in ("missing", "corrupt"):
            store = root / ("bad-" + kind)
            if kind == "corrupt":
                extension = next((template / "prototypes/native_engine_dynamic/.build").glob("_nd_scalar.*"))
                entry = publish(extension, store)
                asset = entry / extension.name
                asset.chmod(0o644)
                with asset.open("ab") as file:
                    file.write(b"corruption-probe")
                asset.chmod(0o444)
            project = root / kind
            shutil.copytree(template, project)
            row = launch(root / "runtime", project, root / (kind + "-diagnostics"), store,
                         loading="immutable", probe="active", expect_failure=True)
            negatives.append(dict(case=kind, **row))
    assert sources() == meta["files"] and libraries() == meta["libraries"]
    for name, data in (("metadata", meta), ("runs", rows), ("negatives", negatives)):
        (output / (name + ".json")).write_text(json.dumps(data, separators=(",", ":")) + "\n")
    certificate = dict(source_sha256=meta["source_sha256"], main_runs=32, cross_path_reproductions=16,
                       ready_cancellations=2, failed_cache_runs=2, request_fingerprint_artifact_identity=True)
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(certificate), flush=True)


def cases():
    return [dict(loading=location, probe=probe, producer=producer, jobs=n, repeat=i)
            for location, probe, producer, n, i in itertools.product(
                ("attempt", "immutable"), ("dormant", "active"),
                ("independent", "feedback"), (5000, 20000), range(3))]


def check_rows(rows, fixture):
    groups = {}
    pids, paths, shared = [], [], []
    for row in rows:
        assert groups.setdefault((row["producer"], row["jobs"]), comparable(row)) == comparable(row)
        verify_loaders(row, fixture)
        pids.extend(d["pid"] for d in row["loaders"])
        paths.extend(d["source"] for d in row["loaders"])
        shared.extend(d["identities"] for d in row["loaders"] if d["mode"] == "immutable")
        if row["probe"] == "active":
            prior.validate_profiles(row["profiles"])
            aggregate = prior.totals(row["profiles"], "run:")
            assert aggregate["native.binary_import"]["count"] == 3
            assert aggregate["backend.advance"]["count"] == sum(r["steps"] for r in row["replications"])
        else:
            assert row["profiles"] == []
    assert len(set(pids)) == len(pids), "execution PID reused within timing matrix"
    assert len(set(paths)) == len(paths), "attempt source path reused"
    assert not shared or all(value == shared[0] for value in shared), "shared binary file identity changed"


def matrix(output):
    output.mkdir(parents=True, exist_ok=False)
    meta, rows, order = metadata(), [], cases()
    random.Random(SEED).shuffle(order)
    meta.update(case_order=order, seed=SEED)
    with tempfile.TemporaryDirectory(prefix="immutable-loading-timing-") as tmp:
        root = Path(tmp).resolve()
        template = create_project(root / "template")
        meta["fixture"] = prepare(root / "runtime", template, root / "assets")
        (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        first_immutable = True
        for index, case in enumerate(order):
            project = root / str(index)
            shutil.copytree(template, project)
            row = launch(root / "runtime", project, root / (str(index) + "-diagnostics"), root / "assets",
                         **{k: v for k, v in case.items() if k != "repeat"})
            row.update(repeat=case["repeat"], first_immutable_use=first_immutable and case["loading"] == "immutable")
            if case["loading"] == "immutable":
                first_immutable = False
            rows.append(row)
            check_rows(rows, meta["fixture"])
            with (output / "matrix.jsonl").open("a") as file:
                file.write(json.dumps(row, separators=(",", ":")) + "\n")
            print(f"{index + 1}/48 {case} {row['process_s']:.3f}s", flush=True)
            shutil.rmtree(project)
    audit(output)


def audit(output):
    meta = json.loads((output / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["libraries"] == libraries()
    rows = [json.loads(line) for line in (output / "matrix.jsonl").read_text().splitlines()]
    assert len(rows) == 48
    order = cases()
    random.Random(SEED).shuffle(order)
    assert meta["case_order"] == order
    for row, case in zip(rows, order):
        assert all(row[key] == value for key, value in case.items())
    check_rows(rows, meta["fixture"])
    summary = []
    for producer, n, probe in itertools.product(("independent", "feedback"), (5000, 20000), ("dormant", "active")):
        selected = [r for r in rows if (r["producer"], r["jobs"], r["probe"]) == (producer, n, probe)]
        values = {}
        for loading in ("attempt", "immutable"):
            group = [r for r in selected if r["loading"] == loading]
            values[loading] = dict(process_s=[r["process_s"] for r in group],
                median_s=statistics.median(r["process_s"] for r in group),
                import_s=statistics.median(sum(d["import_s"] for d in r["loaders"]) for r in group),
                verified_load_s=statistics.median(sum(d["total_s"] for d in r["loaders"]) for r in group))
        summary.append(dict(producer=producer, jobs=n, probe=probe, values=values,
                            reduction=1 - values["immutable"]["median_s"] / values["attempt"]["median_s"]))
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    certificate = dict(source_sha256=meta["source_sha256"], experiments=48, replications=144,
                       fresh_native_processes=len({d["pid"] for r in rows for d in r["loaders"]}),
                       outputs_requests_fingerprints_artifacts_equal=True,
                       evidence={p: hashlib.sha256((output / p).read_bytes()).hexdigest()
                                 for p in ("metadata.json", "matrix.jsonl", "summary.json")})
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(certificate), flush=True)


def audit_experiment(output):
    audit(output / "scaling")
    meta = json.loads((output / "scaling/metadata.json").read_text())
    gate_meta = json.loads((output / "gate/metadata.json").read_text())
    assert gate_meta["files"] == meta["files"] == sources()
    rows = [json.loads(line) for line in (output / "scaling/matrix.jsonl").read_text().splitlines()]
    rejected = []
    for field in ("fingerprint_digest", "artifact_sha256", "backend_state", "kpis", "images", "files"):
        bad = copy.deepcopy(rows)
        if field in ("images", "files"):
            bad[0]["loaders"][0][field] = "corrupt"
        else:
            bad[0]["replications"][0][field] = "corrupt"
        try:
            check_rows(bad, meta["fixture"])
        except (AssertionError, TypeError):
            rejected.append(field)
        else:
            raise AssertionError("accepted corrupted " + field)
    unchanged = 0
    for folder in ("execution-results", "kpi-results", "backend-results", "phase-results"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file():
                assert path.read_bytes() == subprocess.check_output(
                    ["git", "show", "5c16f6f:" + path.relative_to(REPO).as_posix()], cwd=REPO)
                unchanged += 1
    for path in [*(REPO / "src/simulatte").rglob("*.py"), *(ROOT / "csrc").glob("*.[ch]")]:
        assert path.read_bytes() == subprocess.check_output(
            ["git", "show", "5c16f6f:" + path.relative_to(REPO).as_posix()], cwd=REPO)
    result = dict(source_sha256=digest(sources()), historical_evidence_files=unchanged,
                  production_and_C_sources_unchanged=True, rejected_corruptions=rejected,
                  evidence={p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(output.rglob("*")) if p.is_file() and p != output / "verification.json"})
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "evidence"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    child = sub.add_parser("worker")
    for name in ("project", "output", "asset-store"):
        child.add_argument("--" + name, type=Path, required=True)
    child.add_argument("--loading", choices=("attempt", "immutable"), required=True)
    child.add_argument("--probe", choices=("dormant", "active"), required=True)
    child.add_argument("--producer", choices=("independent", "feedback"), required=True)
    child.add_argument("--policy", choices=("fifo", "spt"), default="spt")
    child.add_argument("--jobs", type=int, required=True)
    for name in ("gate", "cancel-ready", "expect-failure"):
        child.add_argument("--" + name, action="store_true")
    for name in ("gate", "matrix", "audit", "audit-experiment"):
        sub.add_parser(name).add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(worker(args), separators=(",", ":")))
    else:
        actions = dict(gate=gate, matrix=matrix, audit=audit, **{"audit-experiment": audit_experiment})
        actions[args.command](args.output)


if __name__ == "__main__":
    main()
