"""Build the validated two-image provider only for native wheels, never sdists."""

import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import struct
import subprocess
import sys
import sysconfig
import tempfile

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return
        if version == "editable":
            raise RuntimeError("Install simulatte-native non-editably; its binaries are built wheel assets")
        if (
            sys.implementation.name != "cpython"
            or sys.platform not in ("darwin", "linux")
            or sysconfig.get_config_var("Py_GIL_DISABLED")
        ):
            raise RuntimeError("simulatte-native requires GIL-enabled CPython on macOS or Linux")
        root = Path(self.root)
        sources = json.loads((root / "sources.json").read_text())
        for name, expected in sources.items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise RuntimeError("Native source manifest mismatch: " + name)
        self.temporary = tempfile.TemporaryDirectory(prefix="simulatte-native-wheel-")
        target = Path(self.temporary.name)
        cc = shlex.split(os.environ.get("CC", "cc"))
        flags = [*cc, "-std=c17", "-O3", "-fPIC", "-Wall", "-Wextra", "-Werror"]
        library = target / (
            "simulatte_native_engine.dylib" if sys.platform == "darwin" else "simulatte_native_engine.so"
        )
        extension = target / ("_nd_scalar" + sysconfig.get_config_var("EXT_SUFFIX"))
        stock = target / "stock_kpi.o"
        commands = [
            [*flags, "-ffp-contract=off", "-c", str(root / "csrc/stock_kpi.c"), "-o", str(stock)],
            [
                *flags,
                *(
                    ["-dynamiclib", "-Wl,-install_name,@loader_path/" + library.name]
                    if sys.platform == "darwin"
                    else ["-shared", "-Wl,-soname," + library.name]
                ),
                str(root / "csrc/dynamic_engine.c"),
                str(stock),
                "-lm",
                "-o",
                str(library),
            ],
            [
                *flags,
                "-I" + sysconfig.get_path("include"),
                *(
                    ["-bundle", "-undefined", "dynamic_lookup"]
                    if sys.platform == "darwin"
                    else ["-shared", "-Wl,-rpath,$ORIGIN"]
                ),
                str(root / "csrc/scalar_module.c"),
                str(library),
                "-o",
                str(extension),
            ],
        ]
        try:
            for command in commands:
                subprocess.run(command, check=True)
            manifest = dict(
                schema=1,
                provider_api=1,
                version=self.metadata.version,
                abi=dict(
                    implementation=sys.implementation.name,
                    soabi=sysconfig.get_config_var("SOABI"),
                    suffix=sysconfig.get_config_var("EXT_SUFFIX"),
                    platform=sys.platform,
                    machine=platform.machine(),
                    bits=struct.calcsize("P") * 8,
                    python=list(sys.version_info[:2]),
                    free_threaded=False,
                ),
                sources=sources,
                files={
                    path.name: dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size)
                    for path in (library, extension)
                },
            )
            marker = target / "manifest.json"
            marker.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
            for path in (library, extension, marker):
                build_data["force_include"][str(path)] = "simulatte_native/_bin/" + path.name
            build_data["pure_python"] = False
            build_data["infer_tag"] = True
        except BaseException:
            self.temporary.cleanup()
            raise

    def finalize(self, version, build_data, artifact_path):
        if hasattr(self, "temporary"):
            self.temporary.cleanup()
