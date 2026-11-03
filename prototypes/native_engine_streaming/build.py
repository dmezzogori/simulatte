"""Build only the research streaming layer; packaged native sources stay unchanged."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
BUILD = ROOT / ".build"


def sources():
    paths = [*ROOT.glob("*.py"), *ROOT.glob("csrc/*"), *ROOT.glob("tests/*")]
    paths += list((REPO / "prototypes/native_engine_dynamic/csrc").glob("*.[ch]"))
    paths += [
        REPO / "src/simulatte/experimental/native.py",
        ROOT / "README.md",
        REPO / "specs/research/2026-11-03-native-streaming-plan.md",
    ]
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths) if p.is_file()}


def build(sanitize=False):
    BUILD.mkdir(exist_ok=True)
    cc = shlex.split(os.environ.get("CC", "cc"))
    base = [*cc, "-std=c17", "-Wall", "-Wextra", "-Werror"]
    flags = (
        [
            "-O1",
            "-g",
            "-fsanitize=address,undefined",
            "-fno-sanitize-recover=all",
            "-fno-omit-frame-pointer",
            "-DND_TEST_ALLOCATIONS",
        ]
        if sanitize
        else ["-O3", "-fPIC"]
    )
    stock = BUILD / ("stock-sanitize.o" if sanitize else "stock.o")
    commands = [[*base, *flags, "-ffp-contract=off", "-c", ROOT / "csrc/stock_metrics.c", "-o", stock]]
    if sanitize:
        target = BUILD / "sanitizer"
        commands += [
            [*base, *flags, ROOT / "csrc/streaming_engine.c", stock, ROOT / "tests/sanitizer.c", "-lm", "-o", target]
        ]
    else:
        target = BUILD / ("streaming_engine.dylib" if sys.platform == "darwin" else "streaming_engine.so")
        extension = BUILD / ("_nb_stream" + sysconfig.get_config_var("EXT_SUFFIX"))
        commands += [
            [
                *base,
                *flags,
                *(
                    ["-dynamiclib", "-Wl,-install_name,@loader_path/" + target.name]
                    if sys.platform == "darwin"
                    else ["-shared", "-Wl,-soname," + target.name]
                ),
                ROOT / "csrc/streaming_engine.c",
                stock,
                "-lm",
                "-o",
                target,
            ]
        ]
        commands += [
            [
                *base,
                *flags,
                "-I" + sysconfig.get_path("include"),
                *(
                    ["-bundle", "-undefined", "dynamic_lookup"]
                    if sys.platform == "darwin"
                    else ["-shared", "-Wl,-rpath,$ORIGIN"]
                ),
                ROOT / "csrc/module.c",
                target,
                "-o",
                extension,
            ]
        ]
    for command in commands:
        subprocess.run(list(map(str, command)), check=True)
    if not sanitize:
        manifest = dict(
            sources=sources(),
            files={
                p.name: dict(sha256=hashlib.sha256(p.read_bytes()).hexdigest(), size=p.stat().st_size)
                for p in [target, extension]
            },
        )
        (BUILD / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sanitize", action="store_true")
    print(build(parser.parse_args().sanitize))
