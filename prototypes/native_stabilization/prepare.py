"""Prepare an isolated installed environment and source project for a host gate."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def main(args):
    work = Path(tempfile.mkdtemp(prefix="simulatte-native-stabilization-"))
    uv = args.uv or shutil.which("uv")
    python = work / "venv/bin/python"
    subprocess.run([uv, "venv", str(work / "venv"), "--python", args.python], check=True)
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--link-mode=copy",
            str(args.core.resolve()) + "[studio]",
            str(args.native.resolve()),
            "psutil",
            "httpx",
        ],
        check=True,
    )
    (work / "project").mkdir()
    for name in ("plant.py", "workloads.py"):
        shutil.copyfile(ROOT / name, work / "project" / name)
    (work / "project/pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py","workloads.py"]\n')
    shutil.copyfile(ROOT / "serve.py", work / "serve.py")
    value = dict(
        work=str(work),
        python=str(python),
        source={
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("plant.py", "workloads.py", "serve.py")
        },
        core_sha256=hashlib.sha256(args.core.read_bytes()).hexdigest(),
        native_sha256=hashlib.sha256(args.native.read_bytes()).hexdigest(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(dict(prepared=True, work=str(work))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--python", default="3.14")
    parser.add_argument("--uv")
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
