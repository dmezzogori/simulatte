"""Data-only declarations for analyses; inspection belongs in an execution child."""

from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from simulatte.model import ParameterSchema, _schema
from .types import identifier

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True, slots=True)
class DatasetRequirement:
    dataset: str
    fields: tuple[str, ...]
    min_replications: int = 0
    require_complete: bool = False

    def __post_init__(self):
        identifier(self.dataset)
        if type(self.fields) is not tuple or len(set(self.fields)) != len(self.fields):
            raise ValueError("Invalid required fields")
        for name in self.fields:
            identifier(name)
        if (
            type(self.min_replications) is not int
            or self.min_replications < 0
            or type(self.require_complete) is not bool
        ):
            raise ValueError("Invalid completeness requirement")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "fields": list(self.fields),
            "min_replications": self.min_replications,
            "require_complete": self.require_complete,
        }


def combine_requirements(*groups: tuple[DatasetRequirement, ...]) -> tuple[DatasetRequirement, ...]:
    combined: dict[str, DatasetRequirement] = {}
    for group in groups:
        for item in group:
            old = combined.get(item.dataset, DatasetRequirement(item.dataset, ()))
            combined[item.dataset] = DatasetRequirement(
                item.dataset,
                tuple(sorted(set(old.fields) | set(item.fields))),
                max(old.min_replications, item.min_replications),
                old.require_complete or item.require_complete,
            )
    return tuple(combined[k] for k in sorted(combined))


@dataclass(frozen=True, slots=True)
class AnalysisDefinition:
    id: str
    entrypoint: str
    source_hash: str
    parameters: ParameterSchema
    requirements: tuple[DatasetRequirement, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entrypoint": self.entrypoint,
            "source_hash": self.source_hash,
            "parameters": self.parameters.to_dict(),
            "requirements": [r.to_dict() for r in self.requirements],
        }


def analysis(*, parameters: type | ParameterSchema, requires: tuple[DatasetRequirement, ...] = ()) -> Callable[[F], F]:
    schema = parameters if isinstance(parameters, ParameterSchema) else _schema(parameters)
    requirements = combine_requirements(requires)

    def decorate(function: F) -> F:
        if not callable(function) or hasattr(function, "__simulatte_analysis__"):
            raise ValueError("Analysis must be an undecorated callable")
        setattr(function, "__simulatte_analysis__", (schema, requirements))
        return function

    return decorate


def get_analysis_definition(
    function: Callable[..., Any], *, id: str, entrypoint: str, source_hash: str
) -> AnalysisDefinition:
    metadata = getattr(function, "__simulatte_analysis__", None)
    if metadata is None:
        raise ValueError("Function is not a decorated analysis")
    return AnalysisDefinition(id, entrypoint, source_hash, *metadata)
