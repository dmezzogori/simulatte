"""Freeze and run the lifecycle fixture from clean installed packages outside Git."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
BASE = "ab5f972be8db44ecb305fcbd67b8bcda6bedadf7"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    paths = [
        *REPO.glob("src/simulatte/**/*.py"),
        *REPO.glob("native/csrc/*"),
        *REPO.glob("native/src/**/*.py"),
        *(ROOT / name for name in ("run.py", "plant.py", "gate.py", "direct.py")),
        ROOT / "README.md",
        REPO / "native/sources.json",
        REPO / "pyproject.toml",
        REPO / "uv.lock",
        REPO / "tests/execution/test_backend.py",
        REPO / "tests/execution/test_native_lifecycle.py",
        REPO / "specs/research/2026-11-04-native-execution-lifecycle-plan.md",
        REPO / "docs/guides/native-engine.md",
        REPO / "prototypes/native_provider_projection/safety.py",
    ]
    return {
        p.relative_to(REPO).as_posix(): sha(p)
        for p in sorted(set(paths))
        if p.is_file() and "__pycache__" not in p.parts
    }


def execute(command, log, *, env=None, cwd=None):
    result = subprocess.run(list(map(str, command)), env=env, cwd=cwd, text=True, capture_output=True, timeout=600)
    log.write_text(result.stdout + result.stderr)
    assert result.returncode == 0, log.read_text()


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    work = Path(tempfile.mkdtemp(prefix="simulatte-native-lifecycle-"))
    frozen = sources()
    core, native = args.core_wheel.resolve(), args.native_wheel.resolve()
    with zipfile.ZipFile(core) as archive:
        for name, expected in frozen.items():
            if name.startswith("src/simulatte/"):
                assert hashlib.sha256(archive.read(name.removeprefix("src/"))).hexdigest() == expected, name
    uv = args.uv or shutil.which("uv")
    python = work / "venv/bin/python"
    execute([uv, "venv", work / "venv", "--python", args.python], output / "venv.log")
    execute([uv, "pip", "install", "--python", python, "--link-mode=copy", core, native], output / "install.log")
    for name in ("plant.py", "gate.py", "direct.py"):
        shutil.copyfile(ROOT / name, work / name)
    runtime = work / "runtime-path"
    runtime.mkdir()
    (runtime / "git").symlink_to(shutil.which("git"))
    env = dict(os.environ, PATH=str(runtime), CC="/no-runtime-compiler")
    meta = dict(
        base=BASE,
        source=frozen,
        source_sha256=hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        core=dict(name=core.name, sha256=sha(core)),
        native=dict(name=native.name, sha256=sha(native)),
        host=platform.platform(),
        workspace=str(work),
        quick=args.quick,
        instrumented=args.instrumented,
    )
    (output / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    result = work / "result.json"
    command = [python, "-I", work / "gate.py", result]
    if args.quick:
        command.append("--quick")
    if args.instrumented:
        command.append("--instrumented")
    execute(command, output / "gate.log", env=env, cwd=work)
    shutil.copyfile(result, output / "result.json")
    shutil.copyfile(work / "events.json", output / "events.json")
    if args.instrumented:
        execute([python, "-I", work / "direct.py", work / "direct.json"], output / "direct.log", env=env, cwd=work)
        shutil.copyfile(work / "direct.json", output / "direct.json")
    assert sources() == frozen
    certificate = {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()}
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(json.dumps(dict(passed=True, output=str(output))), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--core-wheel", type=Path, required=True)
    p.add_argument("--native-wheel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--instrumented", action="store_true")
    p.add_argument("--quick", action="store_true")
    p.add_argument("--python", default="3.14")
    p.add_argument("--uv")
    main(p.parse_args())
