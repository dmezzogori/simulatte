"""Immutable execution identities; no model imports or discovered runtime state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
import math
from pathlib import PurePosixPath
import re
from typing import Any

from simulatte._wire import FrozenMap, MAX_SAFE_INT, freeze
from simulatte.execution.hashing import canonical_hash
from simulatte.stopping import stopping_from_dict


def _hash(value: str) -> None:
    if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError("Expected a SHA-256 hex identity")


def _mapping(value: Any) -> FrozenMap:
    if isinstance(value, FrozenMap):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("Expected a mapping")
    result = freeze(dict(value))
    assert isinstance(result, FrozenMap)
    return result


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _reference(value: str, *, selected: bool) -> None:
    if type(value) is not str:
        raise ValueError("Model reference must be text")
    path, separator, name = value.partition(":")
    if (
        (selected and not separator)
        or (separator and not name.isidentifier())
        or not path
        or "\\" in path
        or "\0" in path
        or PurePosixPath(path).is_absolute()
        or ".." in PurePosixPath(path).parts
        or PurePosixPath(path).as_posix() != path
        or not path.endswith(".py")
    ):
        raise ValueError("Model must be a normalized project-relative Python path with a valid selection")


@dataclass(frozen=True, slots=True, kw_only=True)
class InspectionRequest:
    model: str
    source_hash: str
    runtime: Mapping[str, Any]

    def __post_init__(self):
        _reference(self.model, selected=False)
        _hash(self.source_hash)
        object.__setattr__(self, "runtime", _mapping(self.runtime))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "kind": "inspection",
            "model": self.model,
            "source_hash": self.source_hash,
            "runtime": _plain(self.runtime),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InspectionRequest:
        if (
            set(data) != {"schema", "kind", "model", "source_hash", "runtime"}
            or type(data["schema"]) is not int
            or data["schema"] != 1
            or data["kind"] != "inspection"
        ):
            raise ValueError("Unsupported inspection request schema")
        return cls(model=data["model"], source_hash=data["source_hash"], runtime=data["runtime"])


def inspection_id(request: InspectionRequest) -> str:
    return canonical_hash("inspection-request-v1", request.to_dict())


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionRequest:
    model: str
    source_hash: str
    input_hash: str
    layout_hash: str
    layout_revision: int
    parameters: Mapping[str, Any]
    seed: int
    stopping: Mapping[str, Any]
    level: str = "full"
    kpis: tuple[str, ...] = ()
    runtime: Mapping[str, Any]

    def __post_init__(self):
        for value in (self.source_hash, self.input_hash, self.layout_hash):
            _hash(value)
        _reference(self.model, selected=True)
        if type(self.layout_revision) is not int or not 0 <= self.layout_revision <= MAX_SAFE_INT:
            raise ValueError("Invalid layout revision")
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError("seed must be in [0, 2**63)")
        if self.level not in ("full", "kpi"):
            raise ValueError("Invalid recording level")
        if (
            not isinstance(self.kpis, (tuple, list))
            or any(type(key) is not str or not key for key in self.kpis)
            or len(set(self.kpis)) != len(self.kpis)
        ):
            raise ValueError("KPI selection must contain unique keys")
        object.__setattr__(self, "kpis", tuple(sorted(self.kpis)))
        params = _mapping(self.parameters)
        for key, value in params.items():
            if (
                not key.isidentifier()
                or type(value) not in (str, int, float, bool)
                or (type(value) is float and not math.isfinite(value))
            ):
                raise ValueError("Parameters must be finite typed scalars")
        object.__setattr__(self, "parameters", params)
        object.__setattr__(self, "stopping", _mapping(stopping_from_dict(self.stopping).to_dict()))
        object.__setattr__(self, "runtime", _mapping(self.runtime))

    def to_dict(self) -> dict[str, Any]:
        return {"schema": 1, **{f.name: _plain(getattr(self, f.name)) for f in fields(self)}, "seed": str(self.seed)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionRequest:
        if (
            set(data) != {"schema", *(f.name for f in fields(cls))}
            or type(data["schema"]) is not int
            or data["schema"] != 1
        ):
            raise ValueError("Unsupported execution request schema")
        seed = data["seed"]
        if type(seed) is not str or re.fullmatch("0|[1-9][0-9]{0,18}", seed) is None:
            raise ValueError("Seed must be canonical decimal text")
        return cls(**{k: v for k, v in data.items() if k not in ("schema", "seed")}, seed=int(seed))


def request_id(request: ExecutionRequest) -> str:
    return canonical_hash("execution-request-v1", request.to_dict())


@dataclass(frozen=True, slots=True)
class AttemptKey:
    request_id: str
    attempt_id: str
    generation: int

    def __post_init__(self):
        _hash(self.request_id)
        if type(self.attempt_id) is not str or re.fullmatch("[a-f0-9]{32}", self.attempt_id) is None:
            raise ValueError("Invalid attempt id")
        if type(self.generation) is not int or not 1 <= self.generation <= MAX_SAFE_INT:
            raise ValueError("Invalid attempt generation")


@dataclass(frozen=True, slots=True)
class Revision:
    number: int
    digest: str

    def __post_init__(self):
        _hash(self.digest)
        if type(self.number) is not int or not 0 <= self.number <= MAX_SAFE_INT:
            raise ValueError("Invalid revision number")


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedManifest:
    runtime: Mapping[str, Any]
    dependencies: Mapping[str, Any]
    imports: Mapping[str, Any]
    layout: Mapping[str, Any]
    capabilities: Mapping[str, Any]
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("runtime", "dependencies", "imports", "layout", "capabilities"):
            object.__setattr__(self, name, _mapping(getattr(self, name)))
        if any(type(item) is not str for item in self.warnings):
            raise ValueError("Manifest warnings must be text")
        object.__setattr__(self, "warnings", tuple(self.warnings))

    def to_dict(self) -> dict[str, Any]:
        return {"schema": 1, **{f.name: _plain(getattr(self, f.name)) for f in fields(self)}}
