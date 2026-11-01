"""Run the compiled-boundary suite with ASan/UBSan and scratch-allocation faults."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys

from prototypes.native_engine_dynamic.compiled_build import build


def main():
    if len(sys.argv) == 2:
        import pytest
        from prototypes.native_engine_dynamic import compiled

        spec = importlib.util.spec_from_file_location("_nd_scalar", sys.argv[1])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        compiled.extension = lambda: module
        raise SystemExit(
            pytest.main(
                ["-o", "addopts=", "--import-mode=importlib", str(Path(__file__).with_name("test_compiled.py")), "-q"]
            )
        )
    target = build(sanitize=True)
    env = dict(os.environ, PYTHONMALLOC="malloc", ASAN_OPTIONS="detect_leaks=0", UBSAN_OPTIONS="halt_on_error=1")
    cc = shlex.split(os.environ.get("CC", "cc"))
    if sys.platform == "darwin":
        runtime = subprocess.check_output(
            [*cc, "-print-file-name=libclang_rt.asan_osx_dynamic.dylib"], text=True
        ).strip()
        env["DYLD_INSERT_LIBRARIES"] = runtime
    else:
        runtime = subprocess.check_output([*cc, "-print-file-name=libasan.so"], text=True).strip()
        env["LD_PRELOAD"] = runtime
    subprocess.run(
        [sys.executable, "-m", "prototypes.native_engine_dynamic.tests.run_scalar_sanitizer", str(target)],
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
