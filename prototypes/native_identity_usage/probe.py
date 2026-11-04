"""One fresh installed managed request; wall timing excludes result analysis."""

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.schema import _plain
from simulatte.execution.snapshot import discover_project
from simulatte.stopping import Horizon
import simulatte_native

MODULES = (
    "plant.py",
    "fixed_case.py",
    "coupled_case.py",
    "projection.py",
    "prepared_projection.py",
    "unprepared_projection.py",
    "reference_projection.py",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result_row(result, args):
    trace = Trace.open(result.path / "run.simtrace")
    trace.check()
    assert trace.fingerprint.digest == result.fingerprint["digest"]
    imports = result.manifest["resolved"]["imports"]
    assert not any(name.startswith("prototypes") for name in imports)
    assert "site-packages/simulatte_native/_bin/" in imports["simulatte_native._nd_scalar"]
    directory = result.path / "artifacts"
    files = {p.name: sha(p) for p in sorted(directory.iterdir()) if p.is_file()}
    data = {p.stem: json.loads(p.read_text()) for p in directory.glob("*.json")}
    if "identities" in data:
        sys.path.insert(0, str(Path(__file__).parent))
        import reference_projection as reference

        for phase in ("initial", "final"):
            reference.validate(data["identities"][phase])
        assert data["identities"]["final"]["authority"] == data["native"]
    for p in directory.iterdir():
        if p.is_file():
            target = args.evidence / (files[p.name] + p.suffix)
            if target.exists():
                assert target.read_bytes() == p.read_bytes()
            else:
                shutil.copyfile(p, target)
    return dict(
        fingerprint=_plain(result.fingerprint),
        artifacts=files,
        data=data,
        attempt=result.key.attempt_id,
        request=result.key.request_id,
        helper_origin=imports.get("simulatte.experimental.native_identity"),
        native_origin=imports["simulatte_native._nd_scalar"],
        trace_bytes=(result.path / "run.simtrace").stat().st_size,
    )


def main(args):
    assert shutil.which("cc") is None and shutil.which("clang") is None
    if args.reproduce_request:
        for name in MODULES:
            (args.project / name).unlink()
        shutil.rmtree(args.project / "__pycache__", ignore_errors=True)
        with Coordinator.from_store(args.project, args.reproduce_request) as coordinator:
            result = coordinator.reproduce(args.reproduce_request, timeout=330)
        row = result_row(result, args)
        args.output.write_text(
            json.dumps(dict(result=row, reproduction_of=args.reproduce_request, sources_deleted=True), indent=2) + "\n"
        )
        return
    root = args.output.parent / "project"
    root.mkdir()
    for name in MODULES:
        shutil.copyfile(Path(__file__).with_name(name), root / name)
    (root / "pyproject.toml").write_text("[tool.simulatte.bundle]\ninclude=" + json.dumps(MODULES) + "\n")
    params = dict(
        fixture=args.fixture,
        engine="bounded",
        policy="spt",
        hard_limit=12,
        wip_cap=4,
        burst=3,
        interval=0.5,
        feedback=True,
        record=False,
    )
    started = time.perf_counter()
    with Coordinator(discover_project(root / "plant.py", root)) as coordinator:
        prepared = coordinator.prepare(timeout=60)
        request = coordinator.make_request(
            prepared,
            parameters=params,
            seed=args.seed,
            stopping=Horizon(args.ticks * 0.5, warmup=10).to_dict(),
            level="kpi",
        )
        dispatched = time.perf_counter()
        result = coordinator.wait(coordinator.submit(request, prepared.revision, timeout=300), timeout=330)
        dispatch_seconds = time.perf_counter() - dispatched
    cold_seconds = time.perf_counter() - started
    row = result_row(result, args)
    reproduced = None
    if args.reproduce:
        for name in MODULES:
            (root / name).unlink()
        shutil.rmtree(root / "__pycache__", ignore_errors=True)
        with Coordinator.from_store(root, row["request"]) as coordinator:
            repeated = coordinator.reproduce(row["request"], timeout=330)
            reproduced = result_row(repeated, args)
        for key in ("fingerprint", "artifacts", "data"):
            assert row[key] == reproduced[key]
        assert row["attempt"] != reproduced["attempt"]
    facts = dict(
        python=sys.version,
        platform=platform.platform(),
        gc=__import__("gc").isenabled(),
        distributions={d.metadata["Name"]: d.version for d in metadata.distributions()},
    )
    args.output.write_text(
        json.dumps(
            dict(
                fixture=args.fixture,
                ticks=args.ticks,
                seed=args.seed,
                mode=os.environ["SIMULATTE_PROJECTION_MODE"],
                every=int(os.environ["SIMULATTE_IDENTITIES_QUERY_EVERY"]),
                disabled=os.environ.get("SIMULATTE_IDENTITIES_DISABLE") == "1",
                result=row,
                reproduction=reproduced,
                cold_seconds=cold_seconds,
                dispatch_seconds=dispatch_seconds,
                parent_pid=os.getpid(),
                runtime=facts,
                provider=simulatte_native.info(),
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--fixture", choices=("fixed", "coupled"), required=True)
    p.add_argument("--ticks", type=int, default=32768)
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--reproduce", action="store_true")
    p.add_argument("--reproduce-request")
    p.add_argument("--project", type=Path)
    args = p.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    main(args)
