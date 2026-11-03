"""Instrument both installed images in a private copy; never alter release wheels."""

import argparse
import base64
import csv
import hashlib
from importlib import metadata
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    build = ROOT / (".build/sanitized-" + output.name)
    build.mkdir(exist_ok=False)
    evidence = dict(platform=sys.platform, commands=[])

    def execute(name, command, env=None):
        p = subprocess.run(list(map(str, command)), cwd=REPO, env=env, capture_output=True, timeout=300)
        (output / (name + ".log")).write_bytes(p.stdout + p.stderr)
        evidence["commands"].append(dict(name=name, command=list(map(str, command)), exit_code=p.returncode))
        (output / "checks.json").write_text(json.dumps(evidence, indent=2) + "\n")
        assert p.returncode == 0, p.stdout.decode(errors="replace") + p.stderr.decode(errors="replace")
        return p.stdout.decode().strip()

    uv = shutil.which("uv")
    assert uv
    venv = build / "venv"
    execute("venv", [uv, "venv", venv, "--python", sys.executable])
    python = venv / "bin/python"
    execute(
        "install",
        [
            uv,
            "pip",
            "install",
            "--python",
            python,
            "--link-mode=copy",
            args.core_wheel.resolve(),
            args.native_wheel.resolve(),
            "pytest==" + metadata.version("pytest"),
            "pytest-timeout==" + metadata.version("pytest-timeout"),
        ],
    )
    purelib = Path(execute("path", [python, "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"]))
    directory = purelib / "simulatte_native/_bin"
    marker = directory / "manifest.json"
    manifest = json.loads(marker.read_text())
    library = next(directory.glob("simulatte_native_engine.*"))
    extension = next(directory.glob("_nd_scalar.*"))
    cc = shlex.split(os.environ.get("CC", "cc"))
    flags = [
        *cc,
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
    stock = build / "stock.o"
    execute(
        "stock-build",
        [*flags, "-DND_TEST_ALLOCATIONS", "-ffp-contract=off", "-c", "native/csrc/stock_kpi.c", "-o", stock],
    )
    execute(
        "engine-build",
        [
            *flags,
            "-DND_TEST_ALLOCATIONS",
            *(
                ["-dynamiclib", "-Wl,-install_name,@loader_path/" + library.name]
                if sys.platform == "darwin"
                else ["-shared", "-Wl,-soname," + library.name]
            ),
            "native/csrc/dynamic_engine.c",
            "native/csrc/retention.c",
            stock,
            "-lm",
            "-o",
            library,
        ],
    )
    execute(
        "binding-build",
        [
            *flags,
            "-I" + sysconfig.get_path("include"),
            *(
                ["-bundle", "-undefined", "dynamic_lookup"]
                if sys.platform == "darwin"
                else ["-shared", "-Wl,-rpath,$ORIGIN"]
            ),
            "native/csrc/scalar_module.c",
            "native/csrc/retention_module.c",
            library,
            "-o",
            extension,
        ],
    )
    for path in (library, extension):
        manifest["files"][path.name] = dict(
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size
        )
    marker.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    record = next(purelib.glob("simulatte_native-*.dist-info/RECORD"))
    rows = list(csv.reader(io.StringIO(record.read_text())))
    for row in rows:
        if row[0].startswith("simulatte_native/_bin/"):
            data = (purelib / row[0]).read_bytes()
            row[1] = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
            row[2] = str(len(data))
    with record.open("w", newline="") as file:
        csv.writer(file).writerows(rows)
    runtime = subprocess.check_output(
        [
            *cc,
            "-print-file-name=" + ("libclang_rt.asan_osx_dynamic.dylib" if sys.platform == "darwin" else "libasan.so"),
        ],
        text=True,
    ).strip()
    env = dict(
        os.environ,
        PYTHONMALLOC="malloc",
        ASAN_OPTIONS="detect_leaks=0:abort_on_error=1",
        UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1",
    )
    env["DYLD_INSERT_LIBRARIES" if sys.platform == "darwin" else "LD_PRELOAD"] = runtime
    execute(
        "native-tests",
        [
            python,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-q",
            "tests/experimental/test_native.py",
            "tests/experimental/test_native_retention.py",
            "tests/experimental/test_native_projection.py",
            "prototypes/native_provider_projection/tests",
        ],
        env,
    )
    fault = build / "faults.py"
    fault.write_text("""import ctypes, hashlib
from simulatte.experimental.native import NativeStream, Job
from pathlib import Path
import simulatte_native
directory = Path(simulatte_native.__file__).parent / '_bin'
library = ctypes.CDLL(str(next(p for p in directory.glob('simulatte_native_engine.*') if p.is_file())))
library.nd_test_fail_allocation.argtypes = [ctypes.c_size_t]
for mode in ('full', 'bounded'):
    with NativeStream(2, retention=mode) as stream:
        stream.add(Job(0, (0, 1), (1, 2), 3))
        library.nd_test_fail_allocation(1)
        try:
            stream.state_projection()
        except MemoryError:
            pass
        else:
            raise AssertionError('projection allocation failure was not exercised')
        finally:
            library.nd_test_fail_allocation(0)
        assert stream.state_projection()['native_sha256'] == hashlib.sha256(stream.state_bytes()).hexdigest()
        stream.advance(4)
        assert stream.snapshot()
print('full/bounded projection allocation failures are recoverable; owner busy guards cleared')
""")
    execute("allocation-faults", [python, "-I", fault], env)
    evidence.update(passed=True, manifest=manifest, python_leak_detection=False)
    (output / "checks.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-wheel", type=Path, required=True)
    parser.add_argument("--native-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
