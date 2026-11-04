"""Record installed package and workload identity, without Studio credentials."""

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import sys

import simulatte
import simulatte_native


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(args):
    core, native = Path(simulatte.__file__).parent, Path(simulatte_native.__file__).parent
    assert ".venv" not in core.parts and "site-packages" in core.parts
    prepared = json.loads(args.prepared.read_text())
    value = dict(
        python=sys.version,
        platform=platform.platform(),
        machine=platform.machine(),
        installed={name: metadata.version(name) for name in ("simulatte", "simulatte-native", "uvicorn", "psutil")},
        wheels={name: prepared[name] for name in ("core_sha256", "native_sha256")},
        workload={name: digest(args.work / "project" / name) for name in ("plant.py", "workloads.py")},
        server=digest(args.work / "serve.py"),
        core={p.relative_to(core).as_posix(): digest(p) for p in sorted(core.rglob("*.py"))},
        native={
            p.relative_to(native).as_posix(): digest(p) for p in sorted((native / "_bin").iterdir()) if p.is_file()
        },
    )
    args.output.write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
