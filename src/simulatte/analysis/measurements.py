"""Opt-in measurement declarations and observational collectors.

Callbacks are trusted Python, not a sandbox. They must not mutate simulation
state, schedule events, or draw simulation RNGs. Predicate arguments contain only
copied data. Failures disable the measurement, leaving simulation results intact.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Self

from simulatte.kpi import Collector
from .types import DatasetSchema, Observation, Value, copied, identifier, plain


@dataclass(frozen=True, slots=True)
class Population:
    name: str
    predicate: Callable[[Observation], bool]
    observed_at: str = "observation"
    fields: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.name)
        if not callable(self.predicate) or type(self.observed_at) is not str or not self.observed_at:
            raise ValueError("Population requires a predicate and observation timing")
        if type(self.fields) is not tuple or len(set(self.fields)) != len(self.fields):
            raise ValueError("Invalid population fields")
        for name in self.fields:
            identifier(name)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "observed_at": self.observed_at, "fields": list(self.fields)}


@dataclass(frozen=True, slots=True)
class MeasurementPlan:
    name: str
    datasets: tuple[DatasetSchema, ...]
    populations: tuple[Population, ...] = ()
    parameters: Mapping[str, Value] = field(default_factory=dict)

    def __post_init__(self):
        identifier(self.name)
        for items, kind in ((self.datasets, DatasetSchema), (self.populations, Population)):
            if type(items) is not tuple or any(type(item) is not kind for item in items):
                raise ValueError("Invalid measurement declarations")
            if len({item.name for item in items}) != len(items):
                raise ValueError("Duplicate measurement declaration")
        object.__setattr__(self, "parameters", copied(self.parameters))
        # Selection data is canonical JSON; dataset values have a separate exact codec.
        json.dumps(plain(self.parameters), allow_nan=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "datasets": [x.to_dict() for x in self.datasets],
            "populations": [x.to_dict() for x in self.populations],
            "parameters": plain(self.parameters),
        }


def definition_hash(plan: MeasurementPlan) -> str:
    return hashlib.sha256(
        json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class ObservationCollector(Collector):
    """Explicit records, optionally attached to an environment; no implicit capture.

    Without an output directory rows are retained for the Python caller. Persisted
    collection is configured through ``Environment.configure_measurements``.
    """

    def __init__(
        self,
        schema: DatasetSchema,
        *,
        fields: Mapping[str, Callable[[object], Value]],
        populations: tuple[Population, ...] = (),
    ):
        self.schema = schema
        self.fields = dict(fields)
        self.populations = populations
        known = {f.name for f in schema.fields}
        if set(fields) - known or any(not callable(f) for f in fields.values()):
            raise ValueError("Invalid or undeclared field extractor")
        if len({p.name for p in populations}) != len(populations):
            raise ValueError("Duplicate populations")
        for p in populations:
            if set(p.fields) - known or p.observed_at != schema.observation:
                raise ValueError("Population fields/timing differ from dataset")
        self._env = None
        self._subscription = None
        self._keys = frozenset()
        self._declared = {}
        self._rows: list[Observation] | None = None
        self._memberships: list[tuple[str, ...]] | None = None
        self._disabled = False
        self.failure: str | None = None
        self.output_dir: Path | None = None

    def attach(self, env) -> Self:
        if self._env is not None:
            raise RuntimeError("Observation collector is already attached")
        self._env = env
        plan = env.measurements
        if plan is not None and self.schema not in plan.datasets:
            self._disabled = True
            return self
        self.output_dir = env.measurements_output_dir
        if self.subscribes:
            self._subscription = env.bus.subscribe(self.on_event, self.subscribes)
        env._collectors.append(self)
        env.on_activate(self.on_activate)
        return self

    def record(self, entity: object, *, observed_at: str, values: Mapping[str, Value]) -> None:
        if self._disabled or self.failure is not None or observed_at != self.schema.observation:
            return
        try:
            data = dict(values)
            for name, extract in self.fields.items():
                if name in data:
                    raise ValueError(f"Duplicate observation field {name}")
                data[name] = extract(entity)
            row = self.schema.row(data)
            memberships = []
            for population in self.populations:
                selected = population.predicate(row)
                if type(selected) is not bool:
                    raise ValueError(f"{population.name}: predicate must return bool")
                if selected:
                    memberships.append(population.name)
            if self._rows is None:
                self._rows, self._memberships = [], []
            self._rows.append(row)
            assert self._memberships is not None
            self._memberships.append(tuple(memberships))
        except Exception as error:
            self.failure = f"{self.schema.name}: {type(error).__name__}: {error}"[:4096]

    @property
    def rows(self) -> tuple[Observation, ...]:
        return tuple(self._rows or ())

    @property
    def memberships(self) -> tuple[tuple[str, ...], ...]:
        return tuple(self._memberships or ())

    def scalars(self) -> dict[str, float]:
        return {}
