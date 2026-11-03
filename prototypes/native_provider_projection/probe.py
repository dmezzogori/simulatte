"""Installed-only Coordinator identity gate, with deleted-source reproduction."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import sys

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.schema import _plain
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.experiments import Experiment, Explicit, Replications, run
from simulatte.stopping import Horizon
import simulatte_native

sys.path.insert(0, str(Path(__file__).resolve().parent))
import projection

MODULES = ("plant.py", "fixed_case.py", "coupled_case.py", "projection.py", "reference_projection.py")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parameters(fixture, engine, **kwargs):
    return dict(
        dict(
            fixture=fixture,
            engine=engine,
            policy="spt",
            hard_limit=12,
            wip_cap=4,
            burst=3,
            interval=0.5,
            feedback=True,
            record=True,
        ),
        **kwargs,
    )


def project(root):
    root.mkdir()
    for name in MODULES:
        shutil.copyfile(Path(__file__).with_name(name), root / name)
    (root / "pyproject.toml").write_text("[tool.simulatte.bundle]\ninclude=" + json.dumps(MODULES) + "\n")
    return discover_project(root / "plant.py", root)


def erase(root):
    for name in MODULES:
        (root / name).unlink()
    shutil.rmtree(root / "__pycache__", ignore_errors=True)


def result_row(result, params, seed, warmup):
    trace = Trace.open(result.path / "run.simtrace")
    trace.check()
    assert trace.fingerprint.digest == result.fingerprint["digest"]
    artifacts = result.path / "artifacts"
    hashes = {p.name: sha(p) for p in sorted(artifacts.iterdir()) if p.is_file()}
    proofs = {}
    for name in ("controls", "reports", "frames"):
        path = artifacts / (name + ".jsonl.gz")
        if path.exists():
            proofs[name] = sha(path)
            target = args.evidence / (sha(path) + ".gz")
            if target.exists():
                assert target.read_bytes() == path.read_bytes()
            else:
                shutil.copyfile(path, target)
    imports = result.manifest["resolved"]["imports"]
    assert not any(k.startswith("prototypes") for k in imports)
    origin = imports.get("simulatte_native._nd_scalar")
    if params["engine"] != "python":
        assert origin and "site-packages/simulatte_native/_bin/" in origin

    def read(name):
        file = artifacts / (name + ".json")
        return json.loads(file.read_text()) if file.exists() else None

    physical = read("control" if params["fixture"] == "fixed" else "physical")
    identities, native = read("identities"), read("native")
    if identities is not None and params["engine"] != "python":
        for phase in ("initial", "final"):
            projection.validate(identities[phase])
        assert identities["final"]["authority"] == native
    return dict(
        parameters=params,
        seed=seed,
        warmup=warmup,
        request=result.key.request_id,
        attempt=result.key.attempt_id,
        fingerprint=_plain(result.fingerprint),
        physical=physical,
        reporting=read("reporting") if params["fixture"] == "fixed" else dict(kpis=physical["kpis"]),
        native=native,
        identities=identities,
        proof_sha256=proofs,
        artifact_sha256=hashes,
        origin=origin,
        trace_bytes=(result.path / "run.simtrace").stat().st_size,
    )


def same(first, second, *, identity=True):
    for field in ("fingerprint", "physical", "reporting", "native", "proof_sha256"):
        assert first[field] == second[field], field
    a, b = dict(first["artifact_sha256"]), dict(second["artifact_sha256"])
    if not identity:
        a.pop("identities.json", None)
        b.pop("identities.json", None)
    assert a == b
    if identity:
        assert first["identities"] == second["identities"]


def gate():
    root = args.output.parent / "project"
    spec = project(root)
    rows, selected = [], []
    for fixture, horizon in (("fixed", 96), ("coupled", 80)):
        for warmup in (0, 10, 40):
            definitions = [
                dict(label=policy + "-" + engine, parameters=parameters(fixture, engine, policy=policy))
                for policy in ("fifo", "spt")
                for engine in ("python", "full", "bounded")
            ]
            definition = Experiment(
                name=f"identity {fixture} {warmup}",
                model="plant.py:build",
                level="kpi",
                stopping=Horizon(horizon, warmup=warmup),
                configurations=Explicit(definitions),
                replications=Replications(count=2, base_seed=100),
            )
            status = run(definition, project=root, slots=SlotConfig(local=2), timeout=120)
            assert status["counts"]["succeeded"] == 12 and not status["counts"].get("reused", 0)
            with Coordinator(spec) as coordinator:
                for item in coordinator.store.replications(status["run_id"]):
                    params = definitions[item["configuration"]]["parameters"]
                    row = result_row(coordinator.store.result(item["attempt"]), params, int(item["seed"]), warmup)
                    rows.append(row)
                    if warmup == 10 and row["seed"] == 100:
                        selected.append(row)
    erase(root)
    reproduced = []
    with Coordinator.from_store(root, selected[0]["request"]) as coordinator:
        for first in selected:
            row = result_row(
                coordinator.reproduce(first["request"], timeout=120),
                first["parameters"],
                first["seed"],
                first["warmup"],
            )
            same(first, row)
            assert row["attempt"] != first["attempt"]
            if first["native"] is not None:
                projection.require_reproduction(first["identities"]["final"], row["identities"]["final"])
            reproduced.append(row)
    controls = []
    for kind in ("baseline", "dense"):
        os.environ["SIMULATTE_IDENTITIES_DISABLE"] = str(int(kind == "baseline"))
        os.environ["SIMULATTE_IDENTITIES_QUERY_EVERY"] = str(int(kind == "dense"))
        for first in selected:
            if first["parameters"]["policy"] != "spt" or kind == "dense" and first["native"] is None:
                continue
            twin_root = args.output.parent / (
                kind + "-" + first["parameters"]["fixture"] + "-" + first["parameters"]["engine"]
            )
            twin_spec = project(twin_root)
            with Coordinator(twin_spec) as coordinator:
                prepared = coordinator.prepare(timeout=60)
                request = coordinator.make_request(
                    prepared,
                    parameters=first["parameters"],
                    seed=100,
                    stopping=Horizon(96 if first["parameters"]["fixture"] == "fixed" else 80, warmup=10).to_dict(),
                    level="kpi",
                )
                result = coordinator.wait(coordinator.submit(request, prepared.revision, timeout=120), timeout=150)
                row = result_row(result, first["parameters"], 100, 10)
                same(first, row, identity=kind == "dense")
                controls.append(dict(kind=kind, reference=first["request"], result=row))
    for mode in ("hook", "reference"):
        os.environ["SIMULATTE_PROJECTION_MODE"] = mode
        os.environ["SIMULATTE_IDENTITIES_DISABLE"] = "0"
        os.environ["SIMULATTE_IDENTITIES_QUERY_EVERY"] = "1"
        for first in selected:
            if first["parameters"]["policy"] != "spt" or first["native"] is None:
                continue
            twin_root = args.output.parent / (
                mode + "-" + first["parameters"]["fixture"] + "-" + first["parameters"]["engine"]
            )
            with Coordinator(project(twin_root)) as coordinator:
                prepared = coordinator.prepare(timeout=60)
                request = coordinator.make_request(
                    prepared,
                    parameters=first["parameters"],
                    seed=100,
                    stopping=Horizon(96 if first["parameters"]["fixture"] == "fixed" else 80, warmup=10).to_dict(),
                    level="kpi",
                )
                result = coordinator.wait(coordinator.submit(request, prepared.revision, timeout=120), timeout=150)
                row = result_row(result, first["parameters"], 100, 10)
                same(first, row)
                controls.append(dict(kind=mode, reference=first["request"], result=row))
    return dict(rows=rows, reproductions=reproduced, sources_deleted=True, controls=controls)


def case():
    root = args.output.parent / "project"
    spec = project(root)
    params = parameters(args.fixture, "bounded", record=False)
    with Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=60)
        request = coordinator.make_request(
            prepared,
            parameters=params,
            seed=args.seed,
            stopping=Horizon(args.ticks * 0.5, warmup=10).to_dict(),
            level="kpi",
        )
        result = coordinator.wait(coordinator.submit(request, prepared.revision, timeout=300), timeout=330)
        value = result_row(result, params, args.seed, 10)

    def peak():
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return v if sys.platform == "darwin" else v * 1024

    checkpoints, repeated = [peak()], []
    if args.reproduce_count:
        erase(root)
        with Coordinator.from_store(root, value["request"]) as coordinator:
            for _ in range(args.reproduce_count):
                row = result_row(coordinator.reproduce(value["request"], timeout=300), params, args.seed, 10)
                same(value, row)
                assert row["attempt"] != value["attempt"]
                repeated.append(row)
                checkpoints.append(peak())
    return dict(
        fixture=args.fixture,
        ticks=args.ticks,
        seed=args.seed,
        result=value,
        repetitions=repeated,
        parent_checkpoints=checkpoints,
        store_bytes=sum(p.stat().st_size for p in (root / ".simulatte").rglob("*") if p.is_file()),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--case", action="store_true")
    parser.add_argument("--fixture", default="fixed")
    parser.add_argument("--ticks", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=100)
    parser.add_argument("--reproduce-count", type=int, default=0)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    assert shutil.which("cc") is None and shutil.which("clang") is None
    value = case() if args.case else gate()
    value.update(
        provider=simulatte_native.info(),
        coordinator_pid=os.getpid(),
        runtime=dict(python=sys.version, platform=platform.platform(), machine=platform.machine()),
    )
    args.output.write_text(json.dumps(value, indent=2) + "\n")
