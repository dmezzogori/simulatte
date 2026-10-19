"""Typed analytical values, independent of the trace's safe-number wire format."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeAlias

Value: TypeAlias = None | bool | int | float | str | list["Value"] | Mapping[str, "Value"]


def copied(value: Any, *, depth: int = 0) -> Any:
    """Copy supported values into recursively read-only containers."""
    if depth > 32:
        raise ValueError("Analytical value nesting exceeds 32")
    if value is None or type(value) in (bool, float, str):
        return value
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise ValueError("Analytical int64 out of range")
        return value
    if type(value) in (list, tuple):
        return tuple(copied(item, depth=depth + 1) for item in value)
    if isinstance(value, Mapping) and all(type(key) is str for key in value):
        return MappingProxyType({key: copied(item, depth=depth + 1) for key, item in value.items()})
    raise ValueError(f"Unsupported analytical value type: {type(value).__name__}")


def plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [plain(item) for item in value]
    return value


class Observation(Mapping[str, Any]):
    """Copied read-only data; never a live model object or environment."""

    __slots__ = ("_data",)

    def __init__(self, values: Mapping[str, Any]):
        self._data = copied(values)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def _dtype(value: Any, depth: int = 0) -> Any:
    if depth > 32:
        raise ValueError("Field type nesting exceeds 32")
    if type(value) is str and value in {"null", "bool", "int64", "float64", "string"}:
        return value
    if isinstance(value, Mapping):
        if set(value) == {"list"}:
            return MappingProxyType({"list": _dtype(value["list"], depth + 1)})
        if set(value) == {"record"} and isinstance(value["record"], Mapping):
            if all(type(k) is str and k for k in value["record"]):
                return MappingProxyType(
                    {"record": MappingProxyType({k: _dtype(v, depth + 1) for k, v in value["record"].items()})}
                )
    raise ValueError("Unsupported analytical field type")


def matches(value: Any, dtype: Any) -> bool:
    if isinstance(dtype, str):
        return type(value) is {"null": type(None), "bool": bool, "int64": int, "float64": float, "string": str}[dtype]
    if "list" in dtype:
        return isinstance(value, tuple) and all(matches(x, dtype["list"]) for x in value)
    return (
        isinstance(value, Mapping)
        and set(value) == set(dtype["record"])
        and all(matches(value[k], v) for k, v in dtype["record"].items())
    )


def identifier(name: str) -> None:
    if type(name) is not str or not name.isascii() or not name.isidentifier() or len(name) > 128:
        raise ValueError("Expected a portable identifier of at most 128 characters")


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    dtype: Any
    unit: str | None = None
    nullable: bool = False
    observed_at: str | None = None

    def __post_init__(self):
        identifier(self.name)
        object.__setattr__(self, "dtype", _dtype(self.dtype))
        if type(self.nullable) is not bool or any(
            x is not None and (type(x) is not str or not x) for x in (self.unit, self.observed_at)
        ):
            raise ValueError("Invalid field metadata")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dtype": plain(self.dtype),
            "unit": self.unit,
            "nullable": self.nullable,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class DatasetSchema:
    name: str
    fields: tuple[Field, ...]
    observation: str = "observation"
    cohort: str = "all"
    clip: str = "none"
    censoring: str = "explicit"

    def __post_init__(self):
        identifier(self.name)
        if type(self.fields) is not tuple or not self.fields or any(type(f) is not Field for f in self.fields):
            raise ValueError("Dataset requires typed fields")
        if len({f.name for f in self.fields}) != len(self.fields):
            raise ValueError("Duplicate dataset fields")
        if any(type(x) is not str or not x for x in (self.observation, self.cohort, self.clip, self.censoring)):
            raise ValueError("Invalid observation semantics")

    def row(self, values: Mapping[str, Any]) -> Observation:
        if set(values) != {f.name for f in self.fields}:
            raise ValueError(f"{self.name}: missing or undeclared fields")
        result = Observation(values)
        for f in self.fields:
            value = result[f.name]
            if not (value is None and f.nullable) and not matches(value, f.dtype):
                raise ValueError(f"{self.name}.{f.name}: expected {plain(f.dtype)}")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": [f.to_dict() for f in self.fields],
            "observation": self.observation,
            "cohort": self.cohort,
            "clip": self.clip,
            "censoring": self.censoring,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DatasetSchema:
        return cls(
            data["name"],
            tuple(Field(**f) for f in data["fields"]),
            data["observation"],
            data["cohort"],
            data["clip"],
            data["censoring"],
        )
