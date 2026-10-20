"""Resolve directory and legacy Python launch targets without importing them."""

from pathlib import Path
import keyword

from .types import StudioTarget


def resolve_target(value: str | None, *, cwd: Path) -> StudioTarget:
    cwd = cwd.resolve()
    path = Path(value) if value is not None else cwd
    if not path.is_absolute():
        path = cwd / path
    if path.is_dir():
        return StudioTarget(path.resolve(), None)
    # A Windows drive colon is not an entrypoint separator.
    source, separator, entrypoint = path.name.rpartition(":")
    if separator:
        if not entrypoint.isidentifier() or keyword.iskeyword(entrypoint):
            raise ValueError("Invalid Python entrypoint name")
        path = path.with_name(source)
        if not path.is_file() or path.suffix != ".py":
            raise ValueError(f"Python entrypoint source does not exist: {path}")
    elif not path.exists():
        if path.suffix == ".py":
            raise ValueError(f"Python model file does not exist: {path}")
        path.mkdir(parents=True)
        return StudioTarget(path.resolve(), None)
    if not path.is_file() or path.suffix != ".py":
        raise ValueError(f"Expected a directory or Python model file: {path}")
    path = path.resolve()
    root = next((p for p in (path.parent, *path.parent.parents) if (p / "pyproject.toml").is_file()), path.parent)
    return StudioTarget(root, str(path) + (":" + entrypoint if separator else ""))
