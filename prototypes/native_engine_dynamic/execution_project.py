"""Create a small self-contained research project with snapshotted native assets."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
MODEL = "prototypes/native_engine_dynamic/execution_model.py:build"


def create_project(target):
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise ValueError("native research runtime requires a GIL-enabled interpreter")
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    dynamic = target / "prototypes/native_engine_dynamic"
    static = target / "prototypes/native_engine"
    dynamic.mkdir(parents=True)
    static.mkdir(parents=True)
    modules = (
        "execution_model execution_adapter native_kpis compiled compiled_build build handles "
        "native production studio_trace rich_recording recording validation"
    ).split()
    for name in modules:
        shutil.copyfile(ROOT / (name + ".py"), dynamic / (name + ".py"))
    for name in ("model", "recording"):
        shutil.copyfile(REPO / "prototypes/native_engine" / (name + ".py"), static / (name + ".py"))
    shutil.copytree(ROOT / "csrc", dynamic / "csrc")
    (target / "pyproject.toml").write_text(
        '[tool.simulatte.bundle]\ninclude = ["prototypes/**", "native-assets.json"]\n'
    )
    (target / "layout.json").write_text('{"schema_version":"1.0"}\n')
    build = dynamic / ".build"
    build.mkdir()
    compiler = shlex.split(os.environ.get("CC", "cc"))
    common = [*compiler, "-std=c17", "-O3", "-fPIC", "-Wall", "-Wextra", "-Werror"]
    library = build / ("dynamic_engine.dylib" if sys.platform == "darwin" else "dynamic_engine.so")
    extension = build / ("_nd_scalar" + sysconfig.get_config_var("EXT_SUFFIX"))
    with tempfile.TemporaryDirectory(prefix="native-execution-compile-") as tmp:
        stock = Path(tmp) / "stock_kpi.o"
        commands = [
            [*common, "-ffp-contract=off", "-c", str(dynamic / "csrc/stock_kpi.c"), "-o", str(stock)],
            [
                *common,
                *(
                    ["-dynamiclib", "-Wl,-install_name,@loader_path/" + library.name]
                    if sys.platform == "darwin"
                    else ["-shared", "-Wl,-soname," + library.name]
                ),
                str(dynamic / "csrc/dynamic_engine.c"),
                str(stock),
                "-lm",
                "-o",
                str(library),
            ],
            [
                *common,
                "-I" + sysconfig.get_path("include"),
                *(
                    ["-bundle", "-undefined", "dynamic_lookup"]
                    if sys.platform == "darwin"
                    else ["-shared", "-Wl,-rpath,$ORIGIN"]
                ),
                str(dynamic / "csrc/scalar_module.c"),
                str(library),
                "-o",
                str(extension),
            ],
        ]
        for command in commands:
            subprocess.run(command, check=True, capture_output=True)
    paths = [library, extension, *(dynamic / "csrc").glob("*.[ch]")]
    manifest = dict(
        schema=1,
        files={p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)},
    )
    (target / "native-assets.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return target
