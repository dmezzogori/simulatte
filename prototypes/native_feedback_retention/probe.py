"""Installed-only feedback Coordinator probe, copied outside the checkout."""

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


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parameters(engine, **kwargs):
    return dict(
        dict(engine=engine, policy="spt", hard_limit=12, wip_cap=4, burst=3, interval=0.5, feedback=True, record=True),
        **kwargs,
    )


def project(root):
    root.mkdir()
    shutil.copyfile(Path(__file__).with_name("plant.py"), root / "plant.py")
    (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    return discover_project(root / "plant.py", root)


def result_row(result, params, scenario=None, seed=None):
    trace = Trace.open(result.path / "run.simtrace")
    trace.check()
    assert trace.fingerprint.digest == result.fingerprint["digest"]
    physical = result.path / "artifacts/physical.json"
    artifacts = {p.name: sha(p) for p in sorted((result.path / "artifacts").iterdir()) if p.is_file()}
    proof = result.path / "artifacts/frames.jsonl.gz"
    if proof.exists():
        target = args.evidence / (sha(proof) + ".gz")
        if target.exists():
            assert target.read_bytes() == proof.read_bytes()
        else:
            shutil.copyfile(proof, target)
    imports = result.manifest["resolved"]["imports"]
    assert not any(name.startswith("prototypes") for name in imports)
    origin = imports.get("simulatte_native._nd_scalar")
    if params["engine"] != "python":
        assert origin and "site-packages/simulatte_native/_bin/" in origin
    native = result.path / "artifacts/native.json"
    return dict(
        engine=params["engine"],
        parameters=params,
        scenario=scenario,
        seed=seed,
        request=result.key.request_id,
        attempt=result.key.attempt_id,
        fingerprint=_plain(result.fingerprint),
        physical=json.loads(physical.read_text()),
        physical_sha256=sha(physical),
        native=json.loads(native.read_text()) if native.exists() else None,
        proof_sha256=sha(proof) if proof.exists() else None,
        artifact_sha256=artifacts,
        trace_bytes=(result.path / "run.simtrace").stat().st_size,
        origin=origin,
    )


def gate():
    root = args.output.parent / "project"
    spec = project(root)
    groups = [
        ("short", 17.25, 2, 2, 8, 2),
        ("long", 80, 10, 2, 12, 4),
        ("quarter", 0.25, 0, 1, 12, 4),
        ("tie", 1, 0, 1, 12, 4),
    ]
    rows, selected = [], []
    for label, horizon, warmup, replications, hard, cap in groups:
        definitions = []
        for policy in ("fifo", "spt") if label in ("short", "long") else ("fifo",):
            for feedback in (True, False) if label == "long" else (True,):
                for engine in ("python", "full", "bounded"):
                    name = (label if feedback else "fixed") + "-" + policy
                    definitions.append(
                        dict(
                            label=name + "-" + engine,
                            parameters=parameters(
                                engine, policy=policy, hard_limit=hard, wip_cap=cap, feedback=feedback
                            ),
                        )
                    )
        definition = Experiment(
            name="bounded feedback " + label,
            model="plant.py:build",
            stopping=Horizon(horizon, warmup=warmup),
            level="kpi",
            configurations=Explicit(definitions),
            replications=Replications(count=replications, base_seed=100),
        )
        status = run(definition, project=root, slots=SlotConfig(local=2), timeout=120)
        assert status["counts"]["succeeded"] == len(definitions) * replications
        assert not status["counts"].get("reused", 0)
        with Coordinator(spec) as coordinator:
            for item in coordinator.store.replications(status["run_id"]):
                config = definitions[item["configuration"]]
                scenario = config["label"].rsplit("-", 1)[0]
                row = result_row(
                    coordinator.store.result(item["attempt"]), config["parameters"], scenario, int(item["seed"])
                )
                rows.append(row)
                if label in ("short", "long") and config["parameters"]["feedback"] and int(item["seed"]) == 100:
                    selected.append(row)
    (root / "plant.py").unlink()
    reproduced = []
    with Coordinator.from_store(root, selected[0]["request"]) as coordinator:
        for first in selected:
            row = result_row(
                coordinator.reproduce(first["request"], timeout=120),
                first["parameters"],
                first["scenario"],
                first["seed"],
            )
            for field in ("fingerprint", "physical", "native", "artifact_sha256"):
                assert row[field] == first[field], field
            assert row["attempt"] != first["attempt"]
            reproduced.append(row)
    return dict(rows=rows, reproductions=reproduced, sources_deleted=True)


def case():
    root = args.output.parent / "project"
    spec = project(root)
    params = parameters(args.engine, record=False)
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
        value = result_row(result, params, seed=args.seed)

    def peak():
        n = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return n if sys.platform == "darwin" else n * 1024

    checkpoints = [peak()]
    repeated = []
    if args.reproduce_count:
        (root / "plant.py").unlink()
        with Coordinator.from_store(root, value["request"]) as coordinator:
            for _ in range(args.reproduce_count):
                row = result_row(coordinator.reproduce(value["request"], timeout=300), params, seed=args.seed)
                for field in ("fingerprint", "physical", "native", "artifact_sha256"):
                    assert row[field] == value[field], field
                assert row["attempt"] != value["attempt"]
                repeated.append(row)
                checkpoints.append(peak())
    return dict(
        engine=args.engine,
        ticks=args.ticks,
        seed=args.seed,
        parameters=params,
        result=value,
        repetitions=repeated,
        parent_checkpoints=checkpoints,
        store_bytes=sum(p.stat().st_size for p in (root / ".simulatte").rglob("*") if p.is_file()),
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--case", action="store_true")
    p.add_argument("--engine", default="bounded")
    p.add_argument("--ticks", type=int, default=4096)
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--reproduce-count", type=int, default=0)
    args = p.parse_args()
    args.evidence.mkdir(exist_ok=True, parents=True)
    assert shutil.which("cc") is None and shutil.which("clang") is None
    value = case() if args.case else gate()
    value.update(
        provider=simulatte_native.info(),
        coordinator_pid=os.getpid(),
        runtime=dict(python=sys.version, platform=platform.platform(), machine=platform.machine()),
    )
    args.output.write_text(json.dumps(value, indent=2) + "\n")
