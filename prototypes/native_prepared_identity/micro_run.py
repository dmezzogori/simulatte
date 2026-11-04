"""Freeze and run the serial installed query study; no product edits or builds."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import tempfile
import zipfile

from .common import sources

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
MODULES = (
    "micro_probe.py",
    "unprepared_projection.py",
    "measurement.py",
    "plant.py",
    "fixed_case.py",
    "coupled_case.py",
    "projection.py",
    "reference_projection.py",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cells():
    rows = [
        dict(kind="live", fixture=f, retention=r, policy=p, seed=s, jobs=128, padding=0)
        for f in ("fixed", "coupled")
        for r in ("full", "bounded")
        for p in ("fifo", "spt")
        for s in range(100, 103)
    ]
    rows += [
        dict(kind="history", fixture="synthetic", retention=r, policy="spt", seed=s, jobs=n, padding=0)
        for r in ("full", "bounded")
        for n in (128, 8192, 65536)
        for s in range(100, 103)
    ]
    rows += [
        dict(kind="callback", fixture="synthetic", retention=r, policy="spt", seed=s, jobs=128, padding=n)
        for r in ("full", "bounded")
        for n in (16, 256)
        for s in range(100, 103)
    ]
    return rows


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    workspace = Path(tempfile.mkdtemp(prefix="simulatte-prepared-micro-installed-"))
    core, native = args.core_wheel.resolve(), args.native_wheel.resolve()
    frozen = sources()
    for wheel, prefix in ((core, "src/"), (native, "native/src/")):
        with zipfile.ZipFile(wheel) as archive:
            for name, expected in frozen.items():
                if name.startswith(prefix):
                    assert hashlib.sha256(archive.read(name.removeprefix(prefix))).hexdigest() == expected, name
    with zipfile.ZipFile(native) as archive:
        manifest = json.loads(archive.read("simulatte_native/_bin/manifest.json"))
        assert manifest["provider_api"] == 2 and manifest["sources"] == json.loads(
            (REPO / "native/sources.json").read_text()
        )
    uv = shutil.which("uv")
    assert uv

    def execute(command, log):
        p = subprocess.run(list(map(str, command)), capture_output=True, timeout=300)
        log.write_bytes(p.stdout + p.stderr)
        assert p.returncode == 0, log.read_text()

    venv = workspace / "venv"
    python = venv / "bin/python"
    execute([uv, "venv", venv, "--python", args.python], output / "venv.log")
    execute([uv, "pip", "install", "--python", python, "--link-mode=copy", core, native], output / "install.log")
    for name in MODULES:
        shutil.copyfile(ROOT / name, workspace / name)
    runtime = workspace / "runtime-path"
    runtime.mkdir()
    env = dict(
        os.environ,
        PATH=str(runtime),
        CC="/no-runtime-compiler",
        SIMULATTE_PROJECTION_MODE="hook",
        SIMULATTE_IDENTITIES_DISABLE="0",
        SIMULATTE_IDENTITIES_QUERY_EVERY="0",
        SIMULATTE_FIXED_DIAGNOSTICS="",
        SIMULATTE_FEEDBACK_DIAGNOSTICS="",
        SIMULATTE_IDENTITY_DIAGNOSTICS="",
    )
    cases = cells()
    if args.quick:
        cases = [cases[0], cases[18], cases[30], cases[-1]]
    random.Random(20261104).shuffle(cases)
    metadata = dict(
        base=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        source=frozen,
        source_sha256=hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        core=dict(name=core.name, sha256=sha(core)),
        native=dict(name=native.name, sha256=sha(native)),
        host=platform.platform(),
        workspace=str(workspace),
        cells=cases,
        blocks=args.blocks,
        target_ns=args.target_ns,
        compiler_unavailable=True,
    )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    results = []
    for index, cell in enumerate(cases):
        assert (
            sources() == frozen
            and sha(core) == metadata["core"]["sha256"]
            and sha(native) == metadata["native"]["sha256"]
        )
        target = output / f"cell-{index:02d}"
        command = [
            python,
            "-I",
            workspace / "micro_probe.py",
            target,
            "--blocks",
            str(args.blocks),
            "--target-ns",
            str(args.target_ns),
        ]
        for name, value in cell.items():
            command.extend(["--" + name.replace("_", "-"), str(value)])
        p = subprocess.run(list(map(str, command)), cwd=workspace, env=env, capture_output=True, timeout=300)
        (output / f"cell-{index:02d}.log").write_bytes(p.stdout + p.stderr)
        assert p.returncode == 0, p.stdout.decode(errors="replace") + p.stderr.decode(errors="replace")
        value = json.loads((target / "result.json").read_text())
        assert value["parameters"] == cell
        results.append(dict(cell=index, parameters=cell, pid=value["pid"]))
        (output / "index.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(dict(done=index + 1, of=len(cases), **cell)), flush=True)
    assert sources() == frozen
    cert = dict(
        source_sha256=metadata["source_sha256"],
        files={p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    (output / "verification.json").write_text(json.dumps(cert, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--core-wheel", type=Path, required=True)
    p.add_argument("--native-wheel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--python", default="3.14")
    p.add_argument("--blocks", type=int, default=5)
    p.add_argument("--target-ns", type=int, default=25_000_000)
    p.add_argument("--quick", action="store_true")
    main(p.parse_args())
