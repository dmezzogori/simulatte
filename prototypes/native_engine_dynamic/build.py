"""Build the independent C17 prototype (no Python headers or runtime linkage).

Release: python -m prototypes.native_engine_dynamic.build
Sanitizers: python -m prototypes.native_engine_dynamic.build --sanitize
Then run (from the repository root):
prototypes/native_engine_dynamic/.build/native_sanitizer prototypes/native_engine_dynamic/.build/sanitizer-trace.bin
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / ".build"
LIBRARY = BUILD / ("dynamic_engine.dylib" if sys.platform == "darwin" else "dynamic_engine.so")


def build(*, sanitize: bool = False) -> Path:
    BUILD.mkdir(exist_ok=True)
    compiler = shlex.split(os.environ.get("CC", "cc"))
    flags = ["-std=c17", "-Wall", "-Wextra", "-Wpedantic", "-Werror"]
    source = ROOT / "csrc" / "dynamic_engine.c"
    if sanitize:
        target = BUILD / "native_sanitizer"
        command = [
            *compiler,
            *flags,
            "-O1",
            "-g",
            "-fno-omit-frame-pointer",
            "-fsanitize=address,undefined",
            "-fno-sanitize-recover=all",
            "-DND_TEST_ALLOCATIONS",
            str(source),
            str(ROOT / "tests" / "native_sanitizer.c"),
            "-lm",
            "-o",
            str(target),
        ]
    else:
        target = LIBRARY
        command = [
            *compiler,
            *flags,
            "-O3",
            "-fPIC",
            "-dynamiclib" if sys.platform == "darwin" else "-shared",
            str(source),
            "-lm",
            "-o",
            str(target),
        ]
    print(shlex.join(command), flush=True)
    subprocess.run(command, check=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    print(build(sanitize=args.sanitize))


if __name__ == "__main__":
    main()
