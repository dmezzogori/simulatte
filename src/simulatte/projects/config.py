"""Versioned project declarations and reviewable, preserving TOML edits."""

from dataclasses import asdict, fields
import hashlib
import keyword
import os
from pathlib import Path
import tempfile
import tomllib
from uuid import UUID

from simulatte.execution.durability import replace_durable
from simulatte.execution.snapshot import _path
from .types import AnalysisRegistration, ConfigEdit, ModelRegistration, WorkspaceDefinition


def _uuid(value):
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError("Expected a UUID") from error


def _contained(root, name):
    _path(name)
    if not (root / name).resolve().is_relative_to(root):
        raise ValueError("Path escapes project root")


def _validate(workspace):
    root = workspace.root.resolve()
    identifiers = {_uuid(workspace.id)}
    for registrations in (workspace.models, workspace.analyses):
        keys = set()
        for item in registrations:
            identity = _uuid(item.id)
            if identity in identifiers:
                raise ValueError("Duplicate UUID in project registrations")
            identifiers.add(identity)
            if not isinstance(item.key, str) or not item.key or item.key in keys:
                raise ValueError("Invalid or duplicate registration key")
            keys.add(item.key)
            if not isinstance(item.name, str) or not item.name.strip():
                raise ValueError("Registration name must be nonempty")
            source, separator, function = item.entrypoint.rpartition(":")
            if (
                not separator
                or not function.isidentifier()
                or keyword.iskeyword(function)
                or not source.endswith(".py")
            ):
                raise ValueError("Expected relative Python path:entrypoint")
            _contained(root, source)
            if isinstance(item, ModelRegistration):
                _contained(root, item.layout)
    if workspace.default_model is not None and workspace.default_model not in {m.key for m in workspace.models}:
        raise ValueError("Invalid default_model registration")


def _registration(cls, label, key, value):
    if not isinstance(value, dict):
        raise ValueError(f"{label} registration {key!r} must be a table")
    names = [f.name for f in fields(cls) if f.name != "key"]
    if missing := [name for name in names if name not in value]:
        raise ValueError(f"{label} registration {key!r} is missing required key {missing[0]!r}")
    if unknown := [name for name in value if name not in names]:
        raise ValueError(f"{label} registration {key!r} has unknown key {unknown[0]!r}")
    return cls(key=key, **value)


def _decode(root, data):
    config = data.get("tool", {}).get("simulatte", {})
    if "project" not in config:
        return None
    project = config["project"]
    if type(project.get("schema")) is not int or project["schema"] != 1:
        raise ValueError("Unsupported project schema; use a compatible Simulatte version")
    try:
        workspace = WorkspaceDefinition(
            root,
            project["id"],
            tuple(_registration(ModelRegistration, "Model", k, v) for k, v in config.get("models", {}).items()),
            tuple(_registration(AnalysisRegistration, "Analysis", k, v) for k, v in config.get("analyses", {}).items()),
            project.get("default_model"),
        )
        _validate(workspace)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"Invalid project declaration: {error}") from error
    return workspace


def load_workspace(root: Path) -> WorkspaceDefinition | None:
    root = root.resolve()
    path = root / "pyproject.toml"
    if not path.exists():
        return None
    _contained(root, "pyproject.toml")
    return _decode(root, tomllib.loads(path.read_text(encoding="utf-8")))


def preview_config(root: Path, definition: WorkspaceDefinition) -> ConfigEdit:
    import tomlkit

    root = root.resolve()
    if definition.root.resolve() != root:
        raise ValueError("Workspace root does not match configuration root")
    _validate(definition)
    _contained(root, "pyproject.toml")
    path = root / "pyproject.toml"
    original = path.read_bytes() if path.exists() else None
    document = tomlkit.parse(original.decode("utf-8")) if original is not None else tomlkit.document()
    if original is not None:
        _decode(root, tomllib.loads(original.decode("utf-8")))
    tool = document.setdefault("tool", {})
    config = tool.setdefault("simulatte", {})
    project = {"schema": 1, "id": definition.id}
    if definition.default_model is not None:
        project["default_model"] = definition.default_model
    config["project"] = project
    for key, registrations in [("models", definition.models), ("analyses", definition.analyses)]:
        config[key] = {item.key: {k: v for k, v in asdict(item).items() if k != "key"} for item in registrations}
    return ConfigEdit(
        path,
        None if original is None else hashlib.sha256(original).hexdigest(),
        tomlkit.dumps(document).encode("utf-8"),
    )


def apply_config(edit: ConfigEdit) -> None:
    path = edit.path
    _contained(path.parent.resolve(), path.name)
    _decode(path.parent.resolve(), tomllib.loads(edit.proposed_bytes.decode("utf-8")))
    # Exclusive local edit lock serializes Studio writers; the digest also detects editor changes.
    lock = path.with_name(".simulatte-config.lock")
    lock.open("xb").close()
    try:
        temporary = None
        try:
            current = path.read_bytes() if path.exists() else None
            digest = None if current is None else hashlib.sha256(current).hexdigest()
            if digest != edit.original_sha256:
                raise ValueError("Configuration changed since preview; review a fresh preview")
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(edit.proposed_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                temporary.chmod(path.stat().st_mode & 0o777)
            if (path.read_bytes() if path.exists() else None) != current:
                raise ValueError("Configuration changed during apply")
            replace_durable(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    finally:
        lock.unlink()
