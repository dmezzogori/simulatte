"""Build/check a frozen optional-provider ABI matrix on the current host."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import shlex
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    names = [
        "pyproject.toml",
        "uv.lock",
        "hatch_build.py",
        ".github/workflows/ci.yml",
        "zensical.toml",
        "tests/experimental/test_native.py",
        "tests/execution/test_snapshot.py",
        "tests/execution/test_native_assets.py",
        "tests/execution/test_backend.py",
        "tests/execution/test_driver.py",
        "tests/execution/test_native_assets_integration.py",
        "docs/guides/native-engine.md",
        "docs/guides/execution-backends.md",
        "docs/api/experimental.md",
        "specs/research/2026-11-03-native-package-plan.md",
    ]
    paths = [ROOT / name for name in names]
    paths.extend((ROOT / "src/simulatte").rglob("*.py"))
    paths.extend(
        p
        for p in (ROOT / "native").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".whl", ".gz")
    )
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(set(paths))}


def execute(command, log):
    result = subprocess.run(list(map(str, command)), cwd=ROOT, text=True, capture_output=True, timeout=600)
    log.write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(log.read_text())


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=False)
    files = sources()
    core = args.core_wheel.resolve()
    with zipfile.ZipFile(core) as archive:
        for name, expected in files.items():
            if name.startswith("src/simulatte/"):
                assert hashlib.sha256(archive.read(name.removeprefix("src/"))).hexdigest() == expected, name
    metadata = dict(
        files=files,
        source_sha256=hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        base=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        platform=platform.platform(),
        python=sys.version,
        hostname=platform.node(),
        compiler=subprocess.check_output([*shlex.split(os.environ.get("CC", "cc")), "--version"], text=True),
        core_sha256=sha(core),
    )
    sdists = list(core.parent.glob("simulatte-*.tar.gz"))
    if sdists:
        (sdist,) = sdists
        with tarfile.open(sdist) as archive:
            members = [name.partition("/")[2] for name in archive.getnames()]
            assert not any(name.startswith(("native/", "prototypes/")) for name in members)
        metadata["core_sdist_sha256"] = sha(sdist)
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    rows, seen = [], {}
    uv = args.uv or shutil.which("uv")
    assert uv
    for version in args.pythons:
        name = "cp" + version.replace(".", "")
        build = artifacts / name
        execute([uv, "build", ROOT / "native", "--python", version, "--out-dir", build], output / (name + "-build.log"))
        (wheel,) = build.glob("*.whl")
        (sdist,) = build.glob("*.tar.gz")
        with zipfile.ZipFile(wheel) as archive:
            assert (
                archive.read("simulatte_native/__init__.py")
                == (ROOT / "native/src/simulatte_native/__init__.py").read_bytes()
            )
            marker = json.loads(archive.read("simulatte_native/_bin/manifest.json"))
            assert marker["sources"] == json.loads((ROOT / "native/sources.json").read_text())
        execute(
            [
                sys.executable,
                ROOT / "native/check_install.py",
                "--uv",
                uv,
                "--python",
                version,
                "--core-wheel",
                core,
                "--native-wheel",
                wheel,
                "--output",
                output / name,
            ],
            output / (name + "-install.log"),
        )
        installed = json.loads((output / name / "installed.json").read_text())
        for row in installed["rows"]:
            key = row["configuration"], row["seed"]
            assert seen.setdefault(key, row["kpis"]) == row["kpis"], key
        rows.append(
            dict(
                python=version,
                wheel=wheel.name,
                wheel_sha256=sha(wheel),
                sdist_sha256=sha(sdist),
                managed_runs=len(installed["rows"]),
                reproductions=len(installed["source_deleted_reproductions"]),
            )
        )
        print(json.dumps(rows[-1]), flush=True)
    assert files == sources(), "sources changed during ABI matrix"
    certificate = dict(
        source_sha256=metadata["source_sha256"],
        rows=rows,
        exact_seeded_kpis_across_abis=True,
        evidence={p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    (output / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--pythons", nargs="+", default=["3.11", "3.14", "3.15"])
    parser.add_argument("--uv")
    main(parser.parse_args())
