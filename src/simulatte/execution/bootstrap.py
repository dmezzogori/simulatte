"""Trusted -I -S child bootstrap; standard-library-only until roots are installed.

The supervisor supplies configuration over a private channel. The small stdin
inspection entry is also useful for isolated bootstrap conformance checks.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any


def load_model(reference: str, roots: tuple[Path, ...], *, source_root: Path | None = None):
    from simulatte.model import get_model_spec

    source_root = roots[0] if source_root is None else source_root
    relative, separator, selected = reference.partition(":")
    candidate = (source_root / relative).resolve()
    if not candidate.is_relative_to(source_root) or candidate.suffix != ".py" or not candidate.is_file():
        raise ValueError("Model reference must name a Python file inside the snapshot")
    # Package context follows declared import roots, including namespace packages.
    # Prefer the closest root so a configured src directory is not itself a package.
    # Root-level scripts and paths that cannot be module names retain private names.
    module_name = "_simulatte_model"
    containing = [root for root in roots if candidate.is_relative_to(root)]
    if containing:
        import_root = max(containing, key=lambda root: len(root.parts))
        parts = candidate.relative_to(import_root).with_suffix("").parts
        if len(parts) > 1 and all(part.isidentifier() for part in parts):
            module_name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    spec = importlib.util.spec_from_file_location(module_name, candidate)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load model file")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    models: list[dict[str, Any]] = []
    for name, build in vars(module).items():
        if getattr(build, "__module__", None) != module_name or not hasattr(build, "__simulatte_model__"):
            continue
        metadata = get_model_spec(build)
        models.append(
            {
                "name": name,
                "description": metadata.description,
                "parameters": metadata.schema.to_dict(),
                "inputs": list(metadata.inputs),
                **(
                    {"measurement_plans": [p.to_dict() for p in metadata.measurement_plans]}
                    if metadata.measurement_plans
                    else {}
                ),
                "time_unit": metadata.time_unit,
                "stopping": metadata.stop.to_dict()
                if metadata.stop is not None
                else {"type": "horizon", "horizon": metadata.horizon, "warmup": metadata.warmup},
            }
        )
    models.sort(key=lambda item: item["name"])
    if not models:
        raise ValueError("No decorated model found")
    names = [item["name"] for item in models]
    if separator and selected not in names:
        raise ValueError("Unknown model; available: " + ", ".join(names))
    if not separator and len(models) != 1:
        raise ValueError("Select a model by name; available: " + ", ".join(names))
    selection = selected if separator else names[0]
    return {"models": models, "selected": selection}, getattr(module, selection)


def inspect_model(reference: str, roots: tuple[Path, ...]) -> dict[str, Any]:
    return load_model(reference, roots)[0]


def load_configuration(config: dict[str, Any]):
    snapshot = tuple(Path(root).resolve() for root in config["snapshot_roots"])
    external = tuple(Path(root).resolve() for root in config["external_roots"])
    source_root = Path(config["snapshot_root"]).resolve() if "snapshot_root" in config else snapshot[0]
    # -S preserves stdlib roots even where venv sys.prefix differs across Python
    # versions. External package roots are supplied by the supervising runtime.
    standard = tuple(Path(root).resolve() for root in sys.path if root and Path(root).exists())
    # The entry already prepended the snapshot roots; they are not external. On remote hosts the
    # project root is the snapshot root itself (design 9), which no external root may contain.
    standard = tuple(root for root in standard if root not in snapshot)
    sys.path[:] = [str(root) for root in (*snapshot, *external, *standard)]
    from simulatte.execution.imports import install_import_policy

    policy = install_import_policy(
        Path(config["project_root"]),
        snapshot,
        (*external, *standard),
        source_root=source_root if "snapshot_root" in config else None,
    )
    os.chdir(source_root)
    opened: set[str] = set()
    active = True

    def audit_open(event, args):
        if active and event == "open" and args and isinstance(args[0], (str, bytes)):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.is_relative_to(source_root):
                opened.add(path.relative_to(source_root).as_posix())

    sys.addaudithook(audit_open)
    try:
        result, build = load_model(config["reference"], snapshot, source_root=source_root)
        declarations = {name for model in result["models"] for name in model["inputs"]}
        if opened & declarations:
            raise ValueError("Declared data inputs must be read during build, not model import")
        result["imports"] = policy.audit()
        return result, build, policy
    finally:
        active = False


def inspect_configuration(config: dict[str, Any]) -> dict[str, Any]:
    return load_configuration(config)[0]


def _worker_entry(port: int, token: str) -> int:
    import socket
    import struct

    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.sendall(token.encode("ascii") + b"\n")

        def read_exact(size):
            data = bytearray()
            while len(data) < size:
                part = connection.recv(size - len(data))
                if not part:
                    raise ValueError("Incomplete child configuration")
                data.extend(part)
            return bytes(data)

        size = struct.unpack(">I", read_exact(4))[0]
        if not 1 <= size <= 256 << 10:
            raise ValueError("Invalid child configuration length")
        config = json.loads(read_exact(size))
        sys.path[:0] = [*config["snapshot_roots"], *config["external_roots"]]
        from simulatte.execution.child import serve

        connection.settimeout(None)
        return serve(config, connection)


def main() -> int:
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--child":
            return _worker_entry(int(sys.argv[2]), os.environ.pop("SIMULATTE_CHILD_TOKEN"))
        raw = sys.stdin.buffer.read((256 << 10) + 1)
        if len(raw) > 256 << 10:
            raise ValueError("Bootstrap configuration exceeds 256 KiB")
        config = json.loads(raw)
        # Private protocol output is kept separate from Python-level import noise.
        # OS descriptor capture/diagnostic bounds belong to the worker supervisor.
        with contextlib.redirect_stdout(sys.stderr):
            result = inspect_configuration(config)
        sys.stdout.write(json.dumps(result, allow_nan=False))
        return 0
    except (OSError, ValueError, ImportError, TypeError, KeyError, RecursionError) as error:
        print(f"{type(error).__name__}: {str(error)[:4096]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
