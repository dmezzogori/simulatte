"""Typed, data-only outputs; table iterators are spooled when emitted."""

from __future__ import annotations
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from .types import DatasetSchema, Value


@dataclass(frozen=True)
class Number:
    value: int | float | None
    unit: str | None = None
    metadata: Mapping[str, Value] = field(default_factory=dict)


@dataclass(frozen=True)
class Table:
    schema: DatasetSchema
    rows: Iterable[Mapping[str, Value]]
    metadata: Mapping[str, Value] = field(default_factory=dict)


@dataclass(frozen=True)
class Distribution:
    summary: Mapping[str, Value]
    dataset_ref: str | None = None
    metadata: Mapping[str, Value] = field(default_factory=dict)


@dataclass(frozen=True)
class Figure:
    kind: str
    payload: Any
    metadata: Mapping[str, Value] = field(default_factory=dict)
    renderer: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Image:
    path: Path
    media_type: str
    metadata: Mapping[str, Value] = field(default_factory=dict)


AnalysisOutput = Number | Table | Distribution | Figure | Image


@dataclass(frozen=True)
class OutputDescriptor:
    id: str
    name: str
    kind: str
    artifacts: tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any]
    renderer: Mapping[str, str] | None = None

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "artifacts": list(self.artifacts),
            "metadata": dict(self.metadata),
            "renderer": self.renderer,
        }


@dataclass(frozen=True)
class OutputManifest:
    execution_id: str
    outputs: tuple[OutputDescriptor, ...]
    provenance: Mapping[str, Any]
    schema: Literal["analysis-output-v1"] = "analysis-output-v1"

    def to_dict(self):
        return {
            "schema": self.schema,
            "execution_id": self.execution_id,
            "outputs": [o.to_dict() for o in self.outputs],
            "provenance": dict(self.provenance),
        }
