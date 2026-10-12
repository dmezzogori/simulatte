"""Immutable ``experiment-v1`` definitions shared by the Python API, files and Studio (D92).

Definitions are data only: parameter values are checked against a model's schema
when the experiment is expanded against a prepared model, not here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Literal, TypeVar

from simulatte._wire import MAX_SAFE_INT
from simulatte.execution.hashing import canonical_hash
from simulatte.execution.schema import _reference
from simulatte.execution.snapshot import _path
from simulatte.stopping import FinitePopulation, Horizon, stopping_from_dict

_T = TypeVar("_T")

MAX_CONFIGURATIONS = 10_000
MAX_REPLICATIONS = 1_000_000
MAX_GRID_PARAMETERS = 64
MAX_FILE_BYTES = 16 << 20

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SEED = re.compile(r"0|[1-9][0-9]{0,18}")
_KEYS = {"schema", "name", "model", "replications", "configurations", "layouts", "stopping", "level", "kpis", "workers"}
_REQUIRED = {"schema", "name", "model", "replications", "configurations"}


def _text(value: Any, what: str, *, limit: int = 256) -> str:
    if type(value) is not str or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"{what} must be nonempty text of at most {limit} characters")
    return value


def _scalar(value: Any, what: str) -> Any:
    kind = type(value)
    if kind is int and abs(value) > MAX_SAFE_INT:
        raise ValueError(f"{what}: integer exceeds safe range")
    if kind is float and not math.isfinite(value):
        raise ValueError(f"{what}: float must be finite")
    if kind not in (str, int, float, bool):
        raise ValueError(f"{what}: values must be text, numbers or booleans")
    return value


def _names(values: Any, what: str, check: Callable[[Any], _T]) -> tuple[_T, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{what} must be a list")
    result = tuple(check(item) for item in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{what} must be unique")
    return result


@dataclass(frozen=True, slots=True)
class Replications:
    count: int
    base_seed: int = 0

    def __post_init__(self):
        if type(self.count) is not int or self.count < 1:
            raise ValueError("Replication count must be a positive integer")
        if type(self.base_seed) is not int or self.base_seed < 0:
            raise ValueError("base_seed must be a nonnegative integer")
        if self.base_seed + self.count - 1 >= 2**63:
            raise ValueError("Replication seeds exceed 2**63")

    def to_dict(self) -> dict[str, Any]:
        # Wire integers stop at 2**53 - 1; larger seeds use canonical decimal text, as requests do.
        seed = self.base_seed if self.base_seed <= MAX_SAFE_INT else str(self.base_seed)
        return {"count": self.count, "base_seed": seed}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Replications:
        if not isinstance(data, Mapping) or "count" not in data or not set(data) <= {"count", "base_seed"}:
            raise ValueError("replications needs count and optional base_seed")
        seed = data.get("base_seed", 0)
        if type(seed) is str:
            if _SEED.fullmatch(seed) is None or int(seed) <= MAX_SAFE_INT:
                raise ValueError("Text base_seed must be canonical decimal above 2**53 - 1")
            seed = int(seed)
        elif type(seed) is int and seed > MAX_SAFE_INT:
            raise ValueError("base_seed above 2**53 - 1 must be canonical decimal text")
        return cls(data["count"], seed)


@dataclass(frozen=True, slots=True)
class Grid:
    """Cartesian product of parameter values in declaration order."""

    # A mapping of names to value lists, kept as ``((name, values), ...)`` in declaration order.
    parameters: Any

    def __post_init__(self):
        pairs = self.parameters.items() if isinstance(self.parameters, Mapping) else self.parameters
        result = []
        for name, values in pairs:
            if type(name) is not str or not name.isidentifier():
                raise ValueError("Grid parameter names must be identifiers")
            if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
                raise ValueError(f"Grid parameter {name!r} needs a nonempty list of values")
            values = tuple(_scalar(value, name) for value in values)
            if len({(type(value), value) for value in values}) != len(values):
                raise ValueError(f"Grid parameter {name!r} repeats a value")
            result.append((name, values))
        if len({name for name, _ in result}) != len(result):
            raise ValueError("Grid parameters must be unique")
        if len(result) > MAX_GRID_PARAMETERS:
            raise ValueError("Grid exceeds 64 parameters")
        object.__setattr__(self, "parameters", tuple(result))

    @property
    def size(self) -> int:
        return math.prod(len(values) for _, values in self.parameters)

    def to_dict(self) -> dict[str, Any]:
        return {"grid": {name: list(values) for name, values in self.parameters}}


@dataclass(frozen=True, slots=True)
class Explicit:
    """Listed parameter sets, each ``{"parameters": {...}}`` with an optional ``label``."""

    # Mappings ``{"parameters": {...}, "label"?: ...}``, kept as ``((label, ((name, value), ...)), ...)``.
    configurations: Any

    def __post_init__(self):
        if isinstance(self.configurations, (str, bytes)) or not isinstance(self.configurations, Sequence):
            raise ValueError("Explicit configurations must be a list")
        result = []
        for item in self.configurations:
            if not isinstance(item, Mapping) or "parameters" not in item or not set(item) <= {"label", "parameters"}:
                raise ValueError("Explicit configurations need parameters and an optional label")
            label = item.get("label")
            if label is not None and "/" in _text(label, "Configuration label"):
                raise ValueError("Configuration labels cannot contain '/'")
            parameters = item["parameters"]
            if not isinstance(parameters, Mapping):
                raise ValueError("Explicit parameters must be a mapping")
            for name, value in parameters.items():
                if type(name) is not str or not name.isidentifier():
                    raise ValueError("Parameter names must be identifiers")
                _scalar(value, name)
            result.append((label, tuple(parameters.items())))
        if not result:
            raise ValueError("Explicit configurations must not be empty")
        labels = [label for label, _ in result if label is not None]
        if len(set(labels)) != len(labels):
            raise ValueError("Configuration labels must be unique")
        object.__setattr__(self, "configurations", tuple(result))

    @property
    def size(self) -> int:
        return len(self.configurations)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for label, parameters in self.configurations:
            items.append(({} if label is None else {"label": label}) | {"parameters": dict(parameters)})
        return {"explicit": items}


@dataclass(frozen=True, slots=True)
class LayoutVariant:
    label: str
    file: str

    def __post_init__(self):
        if type(self.label) is not str or _NAME.fullmatch(self.label) is None:
            raise ValueError("Layout labels must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
        try:
            _path(self.file)
        except ValueError as error:
            raise ValueError(f"Invalid layout file: {error}") from None

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "file": self.file}


def _variant(item: Any) -> LayoutVariant:
    if type(item) is not LayoutVariant:
        raise ValueError("layouts must be LayoutVariant entries")
    return item


def _configurations(data: Any) -> Grid | Explicit:
    if not isinstance(data, Mapping) or len(data) != 1:
        raise ValueError("configurations must hold exactly one of grid or explicit")
    if "grid" in data:
        if not isinstance(data["grid"], Mapping):
            raise ValueError("grid must be a mapping")
        return Grid(data["grid"])
    if "explicit" in data:
        return Explicit(data["explicit"])
    raise ValueError("configurations must hold exactly one of grid or explicit")


@dataclass(frozen=True, slots=True, kw_only=True)
class Experiment:
    name: str
    model: str
    replications: Replications
    configurations: Grid | Explicit
    layouts: tuple[LayoutVariant, ...] = ()
    stopping: Horizon | FinitePopulation | None = None
    level: str = "kpi"
    kpis: tuple[str, ...] = ()
    workers: tuple[str, ...] = ()

    def __post_init__(self):
        _text(self.name, "Experiment name")
        _reference(self.model, selected=False)
        if type(self.replications) is not Replications:
            raise ValueError("replications must be Replications")
        if type(self.configurations) not in (Grid, Explicit):
            raise ValueError("configurations must be Grid or Explicit")
        layouts: tuple[LayoutVariant, ...] = _names(self.layouts, "layouts", _variant)
        if len({item.label for item in layouts}) != len(layouts) or len({item.file for item in layouts}) != len(
            layouts
        ):
            raise ValueError("Layout labels and files must be unique")
        object.__setattr__(self, "layouts", layouts)
        if isinstance(self.stopping, Mapping):
            object.__setattr__(self, "stopping", stopping_from_dict(self.stopping))
        elif self.stopping is not None and type(self.stopping) not in (Horizon, FinitePopulation):
            raise ValueError("stopping must be a Horizon, FinitePopulation or None")
        if self.level not in ("full", "kpi"):
            raise ValueError("level must be full or kpi")
        kpis = _names(self.kpis, "kpis", lambda key: _text(key, "KPI key", limit=1024))
        object.__setattr__(self, "kpis", tuple(sorted(kpis)))
        object.__setattr__(
            self, "workers", _names(self.workers, "workers", lambda name: _text(name, "Worker name", limit=64))
        )
        if any(_NAME.fullmatch(name) is None for name in self.workers):
            raise ValueError("Worker names must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
        configurations = self.configurations.size * max(1, len(layouts))
        if configurations > MAX_CONFIGURATIONS:
            raise ValueError("Experiment exceeds 10000 configurations")
        if configurations * self.replications.count > MAX_REPLICATIONS:
            raise ValueError("Experiment exceeds 1000000 replications")

    def to_dict(self) -> dict[str, Any]:
        """The canonical definition: every key present, defaults explicit."""
        return {
            "schema": 1,
            "name": self.name,
            "model": self.model,
            "replications": self.replications.to_dict(),
            "configurations": self.configurations.to_dict(),
            "layouts": [item.to_dict() for item in self.layouts],
            "stopping": None if self.stopping is None else self.stopping.to_dict(),
            "level": self.level,
            "kpis": list(self.kpis),
            "workers": list(self.workers),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Experiment:
        if not isinstance(data, Mapping) or not _REQUIRED <= set(data) or not set(data) <= _KEYS:
            raise ValueError("Unsupported experiment definition keys")
        if type(data["schema"]) is not int or data["schema"] != 1:
            raise ValueError("Unsupported experiment schema")
        layouts = data.get("layouts", [])
        if isinstance(layouts, (str, bytes)) or not isinstance(layouts, Sequence):
            raise ValueError("layouts must be a list")
        variants = []
        for item in layouts:
            if not isinstance(item, Mapping) or set(item) != {"label", "file"}:
                raise ValueError("Layout variants need exactly label and file")
            variants.append(LayoutVariant(item["label"], item["file"]))
        stopping = data.get("stopping")
        if stopping is not None and not isinstance(stopping, Mapping):
            raise ValueError("stopping must be null or a stopping policy")
        return cls(
            name=data["name"],
            model=data["model"],
            replications=Replications.from_dict(data["replications"]),
            configurations=_configurations(data["configurations"]),
            layouts=tuple(variants),
            stopping=None if stopping is None else stopping_from_dict(stopping),
            level=data.get("level", "kpi"),
            kpis=data.get("kpis", ()),
            workers=data.get("workers", ()),
        )


def experiment_id(definition: Experiment | Mapping[str, Any]) -> str:
    """Identity of the canonical definition; equals ``RunStore.put_experiment(experiment.to_dict())``."""
    experiment = definition if isinstance(definition, Experiment) else Experiment.from_dict(definition)
    return canonical_hash("experiment-v1", experiment.to_dict())


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate key {key!r} in experiment file")
        result[key] = value
    return result


def _constant(name):
    raise ValueError(f"Non-finite number {name} in experiment file")


def load_experiment(path: str | Path) -> Experiment:
    """Read a ``*.experiment.json`` file strictly: no duplicate keys, no NaN, bounded size."""
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Experiment file exceeds 16 MiB")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
    except UnicodeDecodeError as error:
        raise ValueError("Experiment file is not UTF-8") from error
    if not isinstance(data, dict):
        raise ValueError("Experiment file must hold a JSON object")
    return Experiment.from_dict(data)


def experiment_type(
    stopping: Horizon | FinitePopulation | Mapping[str, Any],
) -> Literal["steady_state", "terminating_horizon", "terminating_finite"]:
    """The C5.5 experiment type, derived from the stopping policy rather than declared."""
    policy = stopping if isinstance(stopping, (Horizon, FinitePopulation)) else stopping_from_dict(stopping)
    if isinstance(policy, FinitePopulation):
        return "terminating_finite"
    return "steady_state" if policy.warmup > 0 else "terminating_horizon"
