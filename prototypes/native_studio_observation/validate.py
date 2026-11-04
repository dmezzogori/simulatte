"""Validate a new pure core with the unchanged installed native companion."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    paths = [
        *REPO.glob("src/simulatte/**/*.py"),
        *REPO.glob("native/*.py"),
        *REPO.glob("native/csrc/*"),
        REPO / "native/sources.json",
        *ROOT.glob("*.py"),
        ROOT / "README.md",
        REPO / "tests/core/test_studio_native.py",
        REPO / "tests/execution/test_backend_observation.py",
        REPO / "tests/experimental/test_native_observation.py",
        REPO / "examples/native_studio/plant.py",
    ]
    return {p.relative_to(REPO).as_posix(): sha(p) for p in sorted(set(paths)) if p.is_file()}


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    frozen = sources()
    core, native = args.core_wheel.resolve(), args.native_wheel.resolve()
    with zipfile.ZipFile(core) as archive:
        for name, expected in frozen.items():
            if name.startswith("src/simulatte/"):
                assert hashlib.sha256(archive.read(name.removeprefix("src/"))).hexdigest() == expected, name
    uv = args.uv or shutil.which("uv")
    env = dict(os.environ, PATH=str(Path(uv).parent) + os.pathsep + os.environ["PATH"])
    work = Path(tempfile.mkdtemp(prefix="native-studio-installed-"))

    def execute(name, command):
        result = subprocess.run(list(map(str, command)), cwd=REPO, env=env, capture_output=True, timeout=600)
        (output / (name + ".log")).write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (output / (name + ".log")).read_text()

    execute(
        "clean-install",
        [
            sys.executable,
            REPO / "native/check_install.py",
            "--python",
            args.python,
            "--core-wheel",
            core,
            "--native-wheel",
            native,
            "--output",
            output / "installed",
            "--uv",
            uv,
        ],
    )
    execute("venv", [uv, "venv", work / "venv", "--python", args.python])
    python = work / "venv/bin/python"
    execute(
        "dependencies",
        [
            uv,
            "pip",
            "install",
            "--python",
            python,
            "--link-mode=copy",
            str(core) + "[studio]",
            native,
            "pytest",
            "pytest-timeout",
            "httpx",
        ],
    )
    execute(
        "regressions",
        [
            python,
            "-m",
            "pytest",
            "-o",
            "addopts=-q",
            "tests/execution/test_backend.py",
            "tests/execution/test_backend_observation.py",
            "tests/execution/test_native_lifecycle.py",
            "tests/experimental/test_native.py",
            "tests/experimental/test_native_capacity.py",
            "tests/experimental/test_native_retention.py",
            "tests/experimental/test_native_projection.py",
            "tests/experimental/test_native_identity.py",
            "tests/experimental/test_native_observation.py",
            "tests/core/test_studio_native.py",
        ],
    )
    module = ast.parse((REPO / "tests/core/test_studio_native.py").read_text())
    source = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "MODEL" for t in node.targets)
    )
    (work / "model.py").write_text(source)
    shutil.copyfile(ROOT / "studio_probe.py", work / "studio_probe.py")
    execute("studio", [python, "-I", work / "studio_probe.py", work / "model.py", work / "studio"])
    shutil.copytree(work / "studio", output / "studio")
    assert sources() == frozen
    metadata = dict(
        passed=True,
        host=platform.platform(),
        python=args.python,
        workspace=str(work),
        source=frozen,
        core=dict(name=core.name, sha256=sha(core)),
        native=dict(name=native.name, sha256=sha(native)),
    )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (output / "verification.json").write_text(
        json.dumps(
            {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()}, indent=2
        )
        + "\n"
    )
    print(json.dumps(dict(passed=True, output=str(output))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--core-wheel", type=Path, required=True)
    parser.add_argument("--native-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv")
    main(parser.parse_args())
