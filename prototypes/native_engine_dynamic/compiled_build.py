"""Build one Python C API boundary linked to the dynamic engine DSO."""

from __future__ import annotations

import os
import argparse
import shlex
import subprocess
import sys
import sysconfig

from .build import BUILD, LIBRARY, ROOT

EXTENSION = BUILD / ("_nd_scalar" + sysconfig.get_config_var("EXT_SUFFIX"))


def build(*, sanitize=False):
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise RuntimeError("this research boundary requires a GIL-enabled interpreter")
    if not LIBRARY.exists():
        raise FileNotFoundError("build the existing dynamic engine first")
    BUILD.mkdir(exist_ok=True)
    target = EXTENSION
    if sanitize:
        target = BUILD / "scalar-sanitize" / EXTENSION.name
        target.parent.mkdir(exist_ok=True)
    command = [
        *shlex.split(os.environ.get("CC", "cc")),
        "-std=c17",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-fPIC",
        *(
            [
                "-O1",
                "-g",
                "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all",
                "-fno-omit-frame-pointer",
                "-DSCALAR_TEST_ALLOCATIONS",
            ]
            if sanitize
            else ["-O3"]
        ),
        "-I" + sysconfig.get_path("include"),
        *(["-bundle", "-undefined", "dynamic_lookup"] if sys.platform == "darwin" else ["-shared"]),
        str(ROOT / "csrc/scalar_module.c"),
        str(LIBRARY),
        "-o",
        str(target),
    ]
    print(shlex.join(command), flush=True)
    subprocess.run(command, check=True)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sanitize", action="store_true")
    print(build(sanitize=parser.parse_args().sanitize))
