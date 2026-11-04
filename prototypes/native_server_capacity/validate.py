"""Build a host ABI and run clean-install plus installed regression gates."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    paths = [
        *REPO.glob("src/simulatte/**/*.py"),
        *REPO.glob("native/csrc/*"),
        *REPO.glob("native/src/**/*.py"),
        *REPO.glob("native/*.py"),
        REPO / "native/sources.json",
        REPO / "tests/experimental/test_native_capacity.py",
        REPO / "docs/guides/native-engine.md",
        REPO / ".github/workflows/ci.yml",
        ROOT / "README.md",
        ROOT / "validate.py",
        ROOT / "faults.py",
    ]
    return {p.relative_to(REPO).as_posix(): sha(p) for p in sorted(set(paths)) if p.is_file()}


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    frozen = sources()
    uv = args.uv or shutil.which("uv")
    env = dict(os.environ)
    env["PATH"] = str(Path(uv).parent) + os.pathsep + env["PATH"]

    def execute(name, command):
        result = subprocess.run(list(map(str, command)), cwd=REPO, env=env, capture_output=True, timeout=600)
        (output / (name + ".log")).write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (output / (name + ".log")).read_text()

    build = ROOT / ".build" / output.name
    build.mkdir(parents=True, exist_ok=False)
    execute("build", [uv, "build", "native", "--wheel", "--python", args.python, "--out-dir", build])
    native = next(build.glob("*.whl"))
    core = args.core_wheel.resolve()
    execute(
        "install-gate",
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
    venv = build / "regressions"
    execute("venv", [uv, "venv", venv, "--python", args.python])
    python = venv / "bin/python"
    execute(
        "dependencies",
        [uv, "pip", "install", "--python", python, "--link-mode=copy", core, native, "pytest", "pytest-timeout"],
    )
    execute(
        "regressions",
        [
            python,
            "-m",
            "pytest",
            "-o",
            "addopts=-q",
            "tests/experimental/test_native_capacity.py",
            "tests/experimental/test_native.py",
            "tests/experimental/test_native_retention.py",
            "tests/experimental/test_native_projection.py",
            "tests/experimental/test_native_identity.py",
            "tests/execution/test_native_lifecycle.py",
        ],
    )
    assert sources() == frozen
    (output / "metadata.json").write_text(
        json.dumps(
            dict(
                passed=True,
                host=platform.platform(),
                python=args.python,
                source=frozen,
                core=dict(name=core.name, sha256=sha(core)),
                native=dict(name=native.name, sha256=sha(native)),
            ),
            indent=2,
        )
        + "\n"
    )
    (output / "verification.json").write_text(
        json.dumps({p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()}, indent=2) + "\n"
    )
    print(json.dumps(dict(passed=True, output=str(output))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--core-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv")
    main(parser.parse_args())
