"""Reproducible Linux correctness/lifecycle gate for declared native bundles."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.native_assets import VerifiedNativeBundle, _images, abi
from simulatte.execution.references import read_document
from simulatte.execution.schema import _plain
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.execution.store import RunStore
from simulatte.experiments import run
from simulatte.stopping import Horizon
from . import assets_experiment as assets
from .execution_project import REPO, ROOT
from .scalar_benchmark import digest

BASE = "0bc4d391fa13554893551670c3268c1f2e036137"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    extra = ("specs/research/2026-11-03-native-linux-plan.md", "pyproject.toml", "uv.lock", "hatch_build.py")
    return {**assets.sources(), **{name: sha(REPO / name) for name in extra}}


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def metadata():
    assert sys.platform.startswith("linux"), "this gate requires Linux"
    files = sources()
    return dict(base=BASE, platform=platform.platform(), machine=platform.machine(),
                hostname=platform.node(), python=sys.version, abi=abi(),
                compiler=subprocess.check_output(["cc", "--version"], text=True),
                os_release=Path("/etc/os-release").read_text(), files=files, source_sha256=digest(files),
                native_assets_before_build=list((ROOT / ".build").glob("*")) != [],
                purpose="correctness/platform validation; no performance claim")


def assert_pair(row):
    assert assets.comparable(row["reference"]["replications"]) == assets.comparable(row["managed"]["replications"])
    for mode in assets.MODELS:
        assets.prior.prior.same_outputs(row[mode]["replications"])


def load_probe(case_path, *, wrong):
    case = json.loads(case_path.read_text())
    handle = VerifiedNativeBundle(case["bundle"]["name"], Path(case["directory"]), Path(case["source_root"]),
                                  case["bundle"], case["abi"])
    extension = next(name for name in handle.files if name.startswith("_nd_scalar."))
    library = next(name for name in handle.files if name.startswith("dynamic_engine."))
    if wrong:
        import ctypes

        member = next(file for file in handle.bundle["files"] if file["name"] == library)
        ctypes.CDLL(str(handle.source_root / member["source"]))
    before = handle.verify()
    try:
        module = handle.load_extension("_nd_scalar", extension, dependencies=(library,))
    except ValueError as error:
        assert wrong and "dependency loaded from an unexpected origin" in str(error)
        result = dict(rejected=True, error=str(error), origin=None)
    else:
        assert not wrong
        result = dict(rejected=False, origin=module.__file__)
    result.update(pid=os.getpid(), expected_directory=str(handle.directory),
                  images=sorted(map(str, _images(library))), byte_identities_unchanged=handle.verify() == before)
    assert result["byte_identities_unchanged"]
    assert (result["images"] == [str(handle.directory / library)]) == (not wrong)
    return result


def dependency_probes(project, root):
    spec = discover_project(project / assets.MODELS["managed"].partition(":")[0], project)
    with Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=60)
        manifest = read_document(prepared.documents["native_assets_ref"], "native-assets")
        lease = coordinator.store._native_assets().acquire(project, manifest)
        try:
            bundle = manifest["bundles"][0]
            case = root / "load-case.json"
            write(case, dict(source_root=str(project), bundle=_plain(bundle), abi=_plain(manifest["abi"]),
                             directory=lease.directories[bundle["name"]]))
            rows = []
            for wrong in (False, True):
                command = [sys.executable, "-m", "prototypes.native_engine_dynamic.linux_gate", "load-probe",
                           "--case", str(case), *( ["--wrong"] if wrong else [])]
                child = subprocess.run(command, text=True, capture_output=True, timeout=30)
                if child.returncode:
                    raise RuntimeError(child.stderr)
                rows.append(json.loads(child.stdout))
            assert rows[0]["pid"] != rows[1]["pid"]
            return rows
        finally:
            lease.close()


def concurrent_slots(project):
    spec = discover_project(project / assets.MODELS["managed"].partition(":")[0], project)
    definition = assets.definition([dict(backend="native", producer="feedback", jobs=20000, verify=False)],
                                   "managed", count=4)
    samples, maximum, pruned = [], 0, False
    with Coordinator(spec, slots=SlotConfig(local=2)) as coordinator:
        identity = coordinator.start_experiment(definition, timeout=90)
        owner = coordinator.store._native_assets()
        with ThreadPoolExecutor(max_workers=1) as pool:
            completion = pool.submit(coordinator.wait_experiment, identity, timeout=120)
            deadline = time.monotonic() + 120
            while not completion.done():
                assert time.monotonic() < deadline
                with owner.lock:
                    active = dict(owner.leases)
                    maximum = max(maximum, sum(active.values()))
                    if sum(active.values()) >= 2 and not pruned:
                        dry = coordinator.store.prune(dry_run=True)
                        actual = coordinator.store.prune()
                        assert not set(active) & set(dry.get("native_assets", []))
                        assert not set(active) & set(actual.get("native_assets", []))
                        assert all((owner.root / address).exists() for address in active)
                        samples.append(dict(active=active, dry_run=dry, actual=actual))
                        pruned = True
                time.sleep(0.01)
            summary = completion.result()
        assert maximum >= 2 and pruned and summary["counts"]["succeeded"] == 4
        assert not owner.leases
        with coordinator.store._transaction() as db:
            slots = [row[0] for row in db.execute(
                "SELECT DISTINCT slot FROM attempts WHERE id IN "
                "(SELECT attempt FROM replications WHERE run=?)", (identity,))]
        assert set(slots) == {"local-1", "local-2"}
    observed = assets.read_results(project, identity, "managed")
    assert len({row["attempt_id"] for row in observed["replications"]}) == 4
    return dict(maximum_simultaneous_leases=maximum, prune_while_active=samples, slots=sorted(slots),
                leases_after_cleanup=0, observed=observed)


def gate(output):
    output.mkdir(parents=True, exist_ok=False)
    meta, rows = metadata(), []
    with tempfile.TemporaryDirectory(prefix="linux-native-gate-") as tmp:
        root = Path(tmp).resolve()
        template = assets.create_project(root / "template")
        meta["binary_manifest"] = json.loads((template / "native-assets.json").read_text())
        meta["clean_fixture_build"] = True
        extension = next((template / "prototypes/native_engine_dynamic/.build").glob("_nd_scalar.*"))
        meta["elf_header"] = subprocess.check_output(["readelf", "-h", str(extension)], text=True)
        meta["elf_dynamic"] = subprocess.check_output(["readelf", "-d", str(extension)], text=True)
        assert "ELF64" in meta["elf_header"] and "dynamic_engine.so" in meta["elf_dynamic"]
        projects = {mode: root / mode for mode in assets.MODELS}
        for project in projects.values():
            shutil.copytree(template, project)
        for producer, policy, stopping in itertools.product(
            ("independent", "feedback"), ("fifo", "spt"),
            (Horizon(1), Horizon(10, warmup=2), Horizon(200, warmup=20)),
        ):
            observed = {}
            for mode, project in projects.items():
                params = [dict(backend=backend, producer=producer, policy=policy, jobs=40, servers=3, wip_cap=2)
                          for backend in ("production", "native")]
                status = run(assets.definition(params, mode, count=2, stopping=stopping), project=project,
                             slots=SlotConfig(local=2), timeout=60)
                observed[mode] = assets.read_results(project, status["run_id"], mode, baseline="production/base")
            row = dict(producer=producer, policy=policy, stopping=stopping.to_dict(), **observed)
            assert_pair(row)
            rows.append(row)
            print(f"Linux native gate {len(rows)}/12 exact outputs, state, fingerprints and artifacts", flush=True)
        project = projects["managed"]
        loads = dependency_probes(project, root)
        parallel = concurrent_slots(project)
        selected = [row for row in rows[-1]["managed"]["replications"] if row["index"] == 0]
        spec = discover_project(project / assets.MODELS["managed"].partition(":")[0], project)
        with Coordinator(spec) as coordinator:
            prepared = coordinator.prepare(timeout=60)
            request = coordinator.make_request(prepared, level="kpi")
            assert coordinator.preview(prepared, request, prepared.layout, timeout=60).reusable
        entry = next((project / ".simulatte/native-assets").iterdir())
        binary = next(entry.glob("_nd_scalar.*"))
        binary.chmod(0o644)
        with binary.open("ab") as file:
            file.write(b"linux-corruption-probe")
        binary.chmod(0o444)
        native_original = next(row for row in selected if row["backend"] == "native")
        with Coordinator.from_store(project, native_original["request_id"]) as coordinator:
            before = len(list((coordinator.store.root / "results").iterdir()))
            try:
                coordinator.reproduce(native_original["request_id"], timeout=60)
            except ExecutionFailure as error:
                failure = str(error)
            else:
                raise AssertionError("corrupted entry accepted")
            after = len(list((coordinator.store.root / "results").iterdir()))
            assert after == before
        with RunStore.open(project / ".simulatte") as store:
            pruned = store.prune()
            assert len(pruned["native_assets"]) == 1
        shutil.rmtree(project / "prototypes")
        reproduced = []
        with Coordinator.from_store(project, selected[0]["request_id"]) as coordinator:
            for original in selected:
                repeated = coordinator.reproduce(original["request_id"], timeout=60)
                assert repeated.fingerprint["digest"] == original["fingerprint_digest"]
                artifact = sha(repeated.path / "artifacts/native-scalar-result.json")
                assert artifact == original["artifact_sha256"]
                reproduced.append(dict(backend=original["backend"], fingerprint=repeated.fingerprint["digest"],
                                       artifact_sha256=artifact, original_attempt=original["attempt_id"],
                                       repeated_attempt=repeated.key.attempt_id))
        lifecycle = dict(loads=loads, concurrent=parallel, ready_preview=True,
                         corruption=dict(error=failure, results_before=before, results_after=after),
                         pruned=pruned, live_source_deleted=True, reproduced=reproduced)
    assert sources() == meta["files"]
    for name, value in (("metadata", meta), ("runs", rows), ("lifecycle", lifecycle)):
        write(output / (name + ".json"), value)
    audit(output)


def validate_rows(rows):
    assert len(rows) == 12
    expected = list(itertools.product(("independent", "feedback"), ("fifo", "spt"),
                                     (Horizon(1), Horizon(10, warmup=2), Horizon(200, warmup=20))))
    attempts = []
    for row, (producer, policy, stopping) in zip(rows, expected):
        assert (row["producer"], row["policy"], row["stopping"]) == (producer, policy, stopping.to_dict())
        assert_pair(row)
        for mode in assets.MODELS:
            replications = row[mode]["replications"]
            assert len(replications) == 4
            assert {(r["backend"], r["seed"]) for r in replications} == set(
                itertools.product(("production", "native"), (100, 101)))
            for replication in replications:
                attempts.append(replication["attempt_id"])
                if mode == "managed":
                    bundle = replication["native_assets"]["bundles"][0]
                    assert bundle["name"] == "engine" and len(bundle["files"]) == 2
                    if replication["backend"] == "native":
                        assert "/native-assets/" + bundle["address"] + "/" in replication["native_origin"]
                else:
                    assert replication["native_assets"] is None
                    if replication["backend"] == "native":
                        assert replication["native_origin"].startswith("snapshot:")
    assert len(attempts) == len(set(attempts)) == 96


def validate_lifecycle(value):
    good, wrong = value["loads"]
    assert not good["rejected"] and good["origin"].startswith(good["expected_directory"] + "/")
    assert len(good["images"]) == 1 and good["images"][0].startswith(good["expected_directory"] + "/")
    assert wrong["rejected"] and "unexpected origin" in wrong["error"]
    assert len(wrong["images"]) == 1 and not wrong["images"][0].startswith(wrong["expected_directory"] + "/")
    assert good["pid"] != wrong["pid"] and all(row["byte_identities_unchanged"] for row in (good, wrong))
    parallel = value["concurrent"]
    assert parallel["maximum_simultaneous_leases"] >= 2 and parallel["leases_after_cleanup"] == 0
    assert parallel["slots"] == ["local-1", "local-2"] and parallel["prune_while_active"]
    for sample in parallel["prune_while_active"]:
        assert sum(sample["active"].values()) >= 2
        for kind in ("dry_run", "actual"):
            assert not set(sample["active"]) & set(sample[kind].get("native_assets", []))
    assert len(parallel["observed"]["replications"]) == 4
    assert value["ready_preview"] and value["live_source_deleted"]
    assert value["corruption"]["results_before"] == value["corruption"]["results_after"]
    assert value["corruption"]["error"] and len(value["pruned"]["native_assets"]) == 1
    assert {row["backend"] for row in value["reproduced"]} == {"production", "native"}
    for row in value["reproduced"]:
        assert row["original_attempt"] != row["repeated_attempt"]


def audit(output):
    meta = json.loads((output / "metadata.json").read_text())
    assert meta["files"] == sources() and meta["source_sha256"] == digest(meta["files"])
    assert meta["clean_fixture_build"] and meta["abi"]["platform"] == "linux"
    rows = json.loads((output / "runs.json").read_text())
    lifecycle = json.loads((output / "lifecycle.json").read_text())
    validate_rows(rows)
    validate_lifecycle(lifecycle)
    for repeated in lifecycle["reproduced"]:
        original = next(r for r in rows[-1]["managed"]["replications"]
                        if r["attempt_id"] == repeated["original_attempt"])
        assert repeated["fingerprint"] == original["fingerprint_digest"]
        assert repeated["artifact_sha256"] == original["artifact_sha256"]
    rejected = []
    for field in ("fingerprint_digest", "artifact_sha256", "backend_state", "kpis", "native_origin", "native_assets"):
        bad = copy.deepcopy(rows)
        native = next(row for row in bad[0]["managed"]["replications"] if row["backend"] == "native")
        native[field] = "corrupt"
        try:
            validate_rows(bad)
        except (AssertionError, TypeError, KeyError):
            rejected.append(field)
        else:
            raise AssertionError("accepted corrupted " + field)
    bad = copy.deepcopy(lifecycle)
    bad["loads"][1]["rejected"] = False
    try:
        validate_lifecycle(bad)
    except AssertionError:
        rejected.append("mapped_dependency_rejection")
    else:
        raise AssertionError("accepted invalid dependency proof")
    unchanged = {}
    paths = [*(REPO / "src/simulatte").rglob("*.py"), *(ROOT / "csrc").glob("*.[ch]")]
    for path in paths:
        name = path.relative_to(REPO).as_posix()
        assert path.read_bytes() == subprocess.check_output(["git", "show", BASE + ":" + name], cwd=REPO)
        unchanged[name] = sha(path)
    historical = 0
    folders = ("kpi-results", "execution-results", "backend-results", "phase-results",
               "loading-results", "assets-results")
    for folder in folders:
        for path in (ROOT / folder).rglob("*"):
            if path.is_file():
                assert path.read_bytes() == subprocess.check_output(
                    ["git", "show", BASE + ":" + path.relative_to(REPO).as_posix()], cwd=REPO)
                historical += 1
    certificate = dict(source_sha256=meta["source_sha256"], main_runs=96, mode_pairs=48,
                       concurrent_native_runs=4, fresh_deleted_source_reproductions=2,
                       native_dependency_probe_children=2, rejected_corruptions=rejected,
                       unchanged_production_and_C=unchanged, historical_evidence_files=historical,
                       evidence={p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*"))
                                 if p.is_file() and p != output / "verification.json"})
    write(output / "verification.json", certificate)
    print(json.dumps({k: v for k, v in certificate.items() if k not in ("unchanged_production_and_C", "evidence")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("gate", "audit"):
        sub.add_parser(command).add_argument("--output", type=Path, required=True)
    probe = sub.add_parser("load-probe")
    probe.add_argument("--case", type=Path, required=True)
    probe.add_argument("--wrong", action="store_true")
    args = parser.parse_args()
    if args.command == "load-probe":
        print(json.dumps(load_probe(args.case, wrong=args.wrong)))
    else:
        {"gate": gate, "audit": audit}[args.command](args.output)


if __name__ == "__main__":
    main()
