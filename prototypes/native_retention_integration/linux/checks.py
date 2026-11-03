"""Linux-only release, sanitizer and clean-install checks, outside timed runs."""

import argparse
import base64
import csv
import hashlib
from importlib import metadata
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / "prototypes/native_retention_integration"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(args):
    assert sys.platform == "linux" and "sitecustomize" not in sys.modules
    output, build = args.output.resolve(), ROOT / ".build/linux"
    output.mkdir(parents=True, exist_ok=True)
    build.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    assert uv
    python = Path(sys.executable).absolute()
    info = dict(
        python=sys.version,
        executable=str(python),
        platform=platform.platform(),
        libc=platform.libc_ver(),
        base=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=REPO).strip(),
        compiler=subprocess.check_output(["gcc", "--version"], text=True),
        commands=[],
    )

    def execute(name, command, *, env=None, timeout=900):
        print(name, flush=True)
        result = subprocess.run(list(map(str, command)), cwd=REPO, env=env, capture_output=True, timeout=timeout)
        log = output / (name + ".log")
        log.write_bytes(result.stdout + result.stderr)
        info["commands"].append(dict(name=name, command=list(map(str, command)), exit_code=result.returncode))
        (output / "checks.json").write_text(json.dumps(info, indent=2) + "\n")
        assert result.returncode == 0, log.read_text(errors="replace")[-6000:]
        return result.stdout.decode()

    if args.phase == "release":
        execute("native-build", [uv, "build", "native", "--python", python, "--out-dir", build / "native"])
        (wheel,) = (build / "native").glob("*.whl")
        execute(
            "install-check",
            [
                python,
                "native/check_install.py",
                "--python",
                python,
                "--core-wheel",
                args.core_wheel.resolve(),
                "--native-wheel",
                wheel,
                "--output",
                output / "install-check",
            ],
        )
        execute("regressions", [python, "-m", "pytest", "--junitxml=" + str(output / "regressions.xml")])
        shutil.copyfile(REPO / "coverage.xml", output / "coverage.xml")
        execute("lint", [REPO / ".venv/bin/ruff", "check", "src", "tests", "native", ROOT])
        execute("format", [REPO / ".venv/bin/ruff", "format", "--check", "src", "tests", "native", ROOT])
        execute("types", [REPO / ".venv/bin/ty", "check", "src"])
        execute("lock", [uv, "lock", "--check"])
    else:
        (wheel,) = (build / "native").glob("*.whl")
        flags = [
            "gcc",
            "-std=c17",
            "-O1",
            "-g",
            "-fPIC",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsanitize=address,undefined",
            "-fno-sanitize-recover=all",
            "-fno-omit-frame-pointer",
        ]
        stock = build / "stock-test-sanitized.o"
        execute(
            "stock-sanitizer-build",
            [*flags, "-DND_TEST_ALLOCATIONS", "-ffp-contract=off", "-c", "native/csrc/stock_kpi.c", "-o", stock],
        )
        executable = build / "retention-sanitizer"
        execute(
            "standalone-sanitizer-build",
            [
                *flags,
                "-DND_TEST_ALLOCATIONS",
                ROOT / "tests/retention_sanitizer.c",
                "native/csrc/dynamic_engine.c",
                "native/csrc/retention.c",
                stock,
                "-lm",
                "-o",
                executable,
            ],
        )
        env = dict(
            os.environ,
            ASAN_OPTIONS="detect_leaks=1:abort_on_error=1",
            UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1",
        )
        execute("standalone-sanitizer", [executable], env=env)
        stock = build / "stock-sanitized.o"
        execute("boundary-stock-build", [*flags, "-ffp-contract=off", "-c", "native/csrc/stock_kpi.c", "-o", stock])
        venv = build / "sanitized-venv"
        execute("boundary-venv", [uv, "venv", venv, "--python", python])
        boundary = venv / "bin/python"
        execute(
            "boundary-install",
            [
                uv,
                "pip",
                "install",
                "--python",
                boundary,
                "--link-mode=copy",
                args.core_wheel.resolve(),
                wheel,
                "pytest==" + metadata.version("pytest"),
                "pytest-timeout==" + metadata.version("pytest-timeout"),
            ],
        )
        purelib = Path(
            execute(
                "boundary-path", [boundary, "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"]
            ).strip()
        )
        directory = purelib / "simulatte_native/_bin"
        manifest = directory / "manifest.json"
        value = json.loads(manifest.read_text())
        library = directory / "simulatte_native_engine.so"
        extension = next(directory.glob("_nd_scalar.*.so"))
        execute(
            "engine-sanitizer-build",
            [
                *flags,
                "-shared",
                "-Wl,-soname," + library.name,
                "native/csrc/dynamic_engine.c",
                "native/csrc/retention.c",
                stock,
                "-lm",
                "-o",
                library,
            ],
        )
        execute(
            "binding-sanitizer-build",
            [
                *flags,
                "-I" + sysconfig.get_path("include"),
                "-shared",
                "-Wl,-rpath,$ORIGIN",
                "native/csrc/scalar_module.c",
                "native/csrc/retention_module.c",
                library,
                "-o",
                extension,
            ],
        )
        for path in (library, extension):
            value["files"][path.name] = dict(sha256=sha(path), size=path.stat().st_size)
        manifest.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        record = next(purelib.glob("simulatte_native-*.dist-info/RECORD"))
        rows = list(csv.reader(io.StringIO(record.read_text())))
        for row in rows:
            if row[0].startswith("simulatte_native/_bin/"):
                path = purelib / row[0]
                row[1] = "sha256=" + base64.urlsafe_b64encode(
                    hashlib.sha256(path.read_bytes()).digest()
                ).decode().rstrip("=")
                row[2] = str(path.stat().st_size)
        with record.open("w", newline="") as file:
            csv.writer(file).writerows(rows)
        (output / "boundary-manifest.json").write_text(json.dumps(value, indent=2) + "\n")
        asan = subprocess.check_output(["gcc", "-print-file-name=libasan.so"], text=True).strip()
        env.update(LD_PRELOAD=asan, PYTHONMALLOC="malloc", ASAN_OPTIONS="detect_leaks=0:abort_on_error=1")
        execute(
            "boundary-tests",
            [
                boundary,
                "-m",
                "pytest",
                "tests/experimental/test_native.py",
                "tests/experimental/test_native_retention.py",
                "-o",
                "addopts=",
                "-q",
            ],
            env=env,
        )
        execute("boundary-elf", ["readelf", "-d", library, extension])
    info["passed"] = True
    (output / "checks.json").write_text(json.dumps(info, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("release", "safety"))
    parser.add_argument("--core-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
