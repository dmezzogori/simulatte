"""Production outcomes and AGV intervals collected only through observation hooks."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import math
from typing import Any, ClassVar, cast

from simulatte.entities import EntityCreated, EntityRetired
from simulatte.intralogistics.agv import AGV, _UTILIZED_STATES
from simulatte.intralogistics.events import AgvStateChanged
from simulatte.psp import PspEntered
from simulatte.job import ProductionJob
from simulatte.shopfloor import JobFinished, ShopFloorEntered, ShopFloor
from .measurements import ObservationCollector, Population
from .types import DatasetSchema, Field, Observation


def _fields(fields, timing):
    return tuple(Field(name, dtype, nullable=True, observed_at=timing) for name, (dtype, _) in fields.items())


def _attributes(fields, entity):
    return Observation({name: getter(entity) for name, (_, getter) in fields.items()})


def _memberships(populations, row):
    result = []
    for population in populations:
        selected = population.predicate(row)
        if type(selected) is not bool:
            raise ValueError(f"{population.name}: predicate must return bool")
        if selected:
            result.append(population.name)
    return result


def _validate_populations(populations, timing, names):
    if len({p.name for p in populations}) != len(populations):
        raise ValueError("Duplicate populations")
    for p in populations:
        if p.observed_at != timing or set(p.fields) - names:
            raise ValueError("Population requires unavailable fields or a different observation boundary")


class JobOutcomeCollector(ObservationCollector):
    """One row per admitted job, finalized at completion or censored at close.

    Arrival membership is evaluated at first PSP/shop-floor admission, before
    release/processing; ``arrival`` records job creation time for cohort semantics.
    Completion membership is unavailable for censored jobs (null attributes, no
    memberships). ``fields`` maps names to ``(dtype, pure_getter)`` pairs.
    """

    subscribes: ClassVar = (PspEntered, ShopFloorEntered, JobFinished)

    def __init__(
        self,
        *,
        fields: Mapping[str, tuple[Any, Callable]] | None = None,
        populations: tuple[Population, ...] = (),
        membership_at="completion",
        cohort="completed_in_window",
        name="job_outcomes",
    ):
        if membership_at not in ("arrival", "completion") or cohort not in (
            "all",
            "completed_in_window",
            "arrived_in_window",
        ):
            raise ValueError("Invalid job observation timing/cohort")
        self._attributes = dict(fields or {})
        self._populations = populations
        self.membership_at = membership_at
        self._pending: dict[str, Any] = {}
        self.limitations: tuple[str, ...] = ()
        base = (
            Field("job_id", "string"),
            Field("arrival", "float64"),
            Field("completion", "float64", nullable=True),
            Field("due", "float64"),
            Field("lateness", "float64", nullable=True),
            Field("included", "bool"),
            Field("censored", "bool"),
            Field("membership_available", "bool"),
            Field("missing_reason", "string", nullable=True),
            Field("populations", {"list": "string"}),
        )
        schema = DatasetSchema(
            name,
            (*base, *_fields(self._attributes, membership_at)),
            observation="completion",
            cohort=cohort,
            censoring="right_at_close",
        )
        population_fields = {f.name for f in schema.fields} - {"populations"}
        if membership_at == "arrival":
            population_fields -= {"completion", "lateness", "included", "censored"}
        _validate_populations(populations, membership_at, population_fields)
        super().__init__(schema, fields={})

    def attach(self, env):
        super().attach(env)
        if self._disabled:
            return self
        completed = 0
        entities = env.entities.live()
        jobs = {job.id: job for job in entities if isinstance(job, ProductionJob)}
        for entity in entities:
            if isinstance(entity, ShopFloor):
                jobs.update((job.id, job) for job in entity.jobs_done)
        for job in jobs.values():
            if isinstance(job, ProductionJob) and job._shopfloor_id is not None:
                if job.finished_at is not None:
                    completed += 1
                elif job.id not in self._pending:
                    values = {
                        "job_id": job.id,
                        "arrival": float(job.created_at),
                        "due": float(job.due_date),
                        "membership_available": self.membership_at == "completion",
                        "missing_reason": None,
                    }
                    if self.membership_at == "arrival":
                        values.update({name: None for name in self._attributes})
                        values.update(populations=[], missing_reason="arrival preceded collector attachment")
                    self._pending[job.id] = Observation(values)
        if completed:
            self.limitations = (
                f"{completed} jobs completed before collector attachment; completion observations unavailable",
            )
        return self

    def on_event(self, event) -> None:
        if self.failure is not None or self._finished:
            return
        try:
            job = cast(Any, self.env.entities.get(event.job))
            if isinstance(event, (PspEntered, ShopFloorEntered)):
                if job.id not in self._pending:
                    values = {
                        "job_id": job.id,
                        "arrival": float(job.created_at),
                        "due": float(job.due_date),
                        "membership_available": True,
                        "missing_reason": None,
                    }
                    if self.membership_at == "arrival":
                        values.update(_attributes(self._attributes, job))
                        values["populations"] = _memberships(self._populations, Observation(values))
                    self._pending[job.id] = Observation(values)
            else:
                self._outcome(job, censored=False)
        except Exception as error:
            self.failure = f"{self.schema.name}: {type(error).__name__}: {error}"[:4096]

    def _outcome(self, job, *, censored):
        values = dict(self._pending.pop(job.id))
        completion = None if censored else float(job.finished_at)
        included = (
            self.schema.cohort == "all"
            or self.schema.cohort == "arrived_in_window"
            and values["arrival"] >= self.env.warmup
            or self.schema.cohort == "completed_in_window"
            and completion is not None
            and completion >= self.env.warmup
        )
        values.update(
            completion=completion,
            lateness=None if censored else completion - values["due"],
            included=included,
            censored=censored,
        )
        if self.membership_at == "completion":
            values["membership_available"] = not censored
            values["missing_reason"] = "job censored before completion" if censored else None
            values.update({name: None for name in self._attributes} if censored else _attributes(self._attributes, job))
            values["populations"] = [] if censored else _memberships(self._populations, Observation(values))
        self.record(None, observed_at="completion", values=values)

    def finish(self) -> None:
        if not self._finished and self.failure is None:
            try:
                for identity in tuple(self._pending):
                    self._outcome(self.env.entities.get(identity), censored=True)
            except Exception as error:
                self.failure = f"{self.schema.name}: {type(error).__name__}: {error}"[:4096]
        self._pending.clear()
        super().finish()


class AgvDurationCollector(ObservationCollector):
    """State intervals with classification copied at interval entry.

    ``observe_boundary(agv)`` explicitly closes/reopens the interval after a
    classification edit that does not change state. No temporal data are inferred
    from final aggregates. Open intervals are clipped to warm-up and closed at finish.
    """

    subscribes: ClassVar = (AgvStateChanged, EntityCreated, EntityRetired)

    def __init__(
        self,
        *,
        fields: Mapping[str, tuple[Any, Callable]] | None = None,
        populations: tuple[Population, ...] = (),
        membership_at="interval_start",
        name="agv_durations",
    ):
        if membership_at != "interval_start":
            raise ValueError("AGV membership must be observed at interval_start")
        self._attributes = dict(fields or {})
        self._populations = populations
        self._intervals: dict[str, Any] = {}
        base = (
            Field("agv_id", "string"),
            Field("state", "string"),
            Field("start", "float64"),
            Field("end", "float64"),
            Field("duration", "float64"),
            Field("included", "bool"),
            Field("clipped", "bool"),
            Field("censored", "bool"),
            Field("populations", {"list": "string"}),
        )
        schema = DatasetSchema(
            name,
            (*base, *_fields(self._attributes, membership_at)),
            observation=membership_at,
            clip="observation_window",
            censoring="right_at_close",
        )
        _validate_populations(populations, membership_at, {"agv_id", "state", "start", *self._attributes})
        super().__init__(schema, fields={})

    def _start(self, agv):
        values = {
            "agv_id": agv.id,
            "state": agv.state.name,
            "start": float(self.env.now),
            **_attributes(self._attributes, agv),
        }
        values["populations"] = _memberships(self._populations, Observation(values))
        self._intervals[agv.id] = Observation(values)

    def _end(self, identity, *, censored=False):
        values = dict(self._intervals.pop(identity))
        start, end = values["start"], float(self.env.now)
        clipped = min(max(start, self.env.warmup), end)
        values.update(
            start=clipped,
            end=end,
            duration=end - clipped,
            included=end > clipped,
            clipped=clipped != start,
            censored=censored,
        )
        self.record(None, observed_at="interval_start", values=values)

    def on_activate(self) -> None:
        try:
            for entity in self.env.entities.live():
                if isinstance(entity, AGV) and entity.id not in self._intervals:
                    self._start(entity)
        except Exception as error:
            self.failure = f"{self.schema.name}: {type(error).__name__}: {error}"[:4096]

    def observe_boundary(self, agv: AGV) -> None:
        if self._disabled or self._finished or self.failure is not None:
            return
        try:
            if agv.env is not self.env:
                raise ValueError("AGV belongs to another environment")
            if agv.id in self._intervals:
                self._end(agv.id)
            self._start(agv)
        except Exception as error:
            self.failure = f"{self.schema.name}: {type(error).__name__}: {error}"[:4096]

    def on_event(self, event) -> None:
        if isinstance(event, AgvStateChanged):
            self.observe_boundary(cast(AGV, self.env.entities.get(event.agv)))
        elif isinstance(event, EntityCreated) and event.kind == "agv" and self.env._activation_started:
            self.observe_boundary(cast(AGV, self.env.entities.get(event.entity)))
        elif isinstance(event, EntityRetired) and event.entity in self._intervals:
            self._end(event.entity, censored=True)

    def finish(self) -> None:
        if not self._finished and self.failure is None:
            for identity in tuple(self._intervals):
                self._end(identity, censored=True)
        self._intervals.clear()
        super().finish()


@dataclass(frozen=True, slots=True)
class PopulationSelection:
    status: str
    rows: tuple[Observation, ...]
    missing_fields: tuple[str, ...] = ()


def select_population(
    rows: Iterable[Mapping[str, Any]],
    population: Population,
    *,
    retained_fields: tuple[str, ...] | None = None,
    temporal: bool = True,
) -> PopulationSelection:
    """Small in-memory posthoc selection; use context.rows for large streaming analyses."""
    selected = []
    if retained_fields is not None:
        missing = set(population.fields) - set(retained_fields)
        if not temporal:
            missing |= set(population.fields) & {"start", "end", "observed_at"}
        if missing:
            return PopulationSelection("insufficient", (), tuple(sorted(missing)))
    for row in rows:
        missing = set(population.fields) - set(row)
        if missing:
            return PopulationSelection("insufficient", (), tuple(sorted(missing)))
        observation = Observation(row)
        if _memberships((population,), observation):
            selected.append(observation)
    return PopulationSelection("available", tuple(selected))


def fleet_utilization(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Descriptive mean of fractions AND ratio of durations; zero windows are missing."""
    totals: dict[str, list[float]] = {}
    utilized = {state.name for state in _UTILIZED_STATES}
    for row in rows:
        duration = row["duration"]
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            raise ValueError("Duration must be finite and nonnegative")
        counts = totals.setdefault(row["agv_id"], [0.0, 0.0])
        counts[0] += duration
        if row["state"] in utilized:
            counts[1] += duration
    fractions = [busy / total for total, busy in totals.values() if total > 0]
    total = math.fsum(t for t, _ in totals.values())
    return {
        "mean_of_vehicle_fractions": math.fsum(fractions) / len(fractions) if fractions else None,
        "ratio_of_durations": math.fsum(b for _, b in totals.values()) / total if total else None,
        "vehicles": len(totals),
        "missing_vehicles": len(totals) - len(fractions),
        "convention": "descriptive_within_run",
    }
