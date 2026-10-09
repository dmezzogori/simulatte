"""KPI declarations, scoped collectors and observation windows (spec §12, global C1.8).

A :class:`KPI` declares a result: its name, unit, kind (a ``series`` of samples, an end-of-run ``scalar``,
or both) and its estimand (observation unit, cohort, aggregation, clipping at the window, censoring,
EMA reset, value without observations). A :class:`Collector` is bound to an owner entity, its *scope*: it
declares its KPIs and the events it subscribes to, keeps its own state, emits ``kpi.sample`` events for its
series and returns its scalars keyed ``"<scope id>/<kpi name>"``, so that two systems sharing an environment
never merge their results. :meth:`Environment.fingerprint` collects the scalars of every attached collector.

The observation :class:`Window` starts at the warm-up set with :meth:`Environment.configure_kpis` (default 0)
and ends where the run stopped: ``[warmup, horizon)`` after ``run(until=horizon)``, ``[warmup, T_end]`` after
a run to exhaustion, ``T_end`` being the time of the last processed event. Time-weighted KPIs use
:class:`TimeWeighted`, which clips a piecewise-constant signal to the window and divides by its length.

Collectors are observers (spec §13): they read simulation objects only through pure getters and never
schedule SimPy events or draw from ``env.rng``. Float sums are exactly rounded (D58).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, ClassVar, Literal, Self

from simulatte._wire import FrozenMap
from simulatte.entities import Entity
from simulatte.events import Event, KpiSample, Subscription

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.environment import Environment

__all__ = ["KPI", "Collector", "TimeWeighted", "Window", "observation_window"]

KpiKind = Literal["series", "scalar"]
Cohort = Literal["completed_in_window", "arrived_in_window", "all"]
Clip = Literal["none", "window"]
Censoring = Literal["exclude", "include"]

_KINDS: frozenset[str] = frozenset({"series", "scalar"})
_COHORTS: frozenset[str] = frozenset({"completed_in_window", "arrived_in_window", "all"})
_CLIPS: frozenset[str] = frozenset({"none", "window"})
_CENSORING: frozenset[str] = frozenset({"exclude", "include"})
_OBSERVED_AGGREGATIONS: frozenset[str] = frozenset({"mean", "sum", "count", "min", "max"})
_MISSING = object()


# ---------------------------------------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KPI:
    """Declaration of a KPI and its estimand (global C1.8).

    - `name`: unique within its collector; no ``/`` or NUL (results are keyed ``"<scope id>/<name>"``).
    - `unit`: unit of the value, for example ``"time"`` or ``"jobs"``.
    - `kind`: ``("scalar",)``, ``("series",)`` or both; a series is sampled with :meth:`Collector.sample`, a
      scalar is returned by :meth:`Collector.scalars`.
    - `observation`: the observation unit, for example ``"job"``, ``"operation"`` or ``"time_weighted"``.
    - `cohort`: which observations count (:meth:`Collector.observe`): ``"completed_in_window"`` (completed at
      or after the warm-up), ``"arrived_in_window"`` (arrived at or after the warm-up) or ``"all"``.
    - `aggregation`: how observations become the scalar; :meth:`Collector.observe` supports ``"mean"``,
      ``"sum"``, ``"count"``, ``"min"`` and ``"max"``, other values are computed by the collector.
    - `clip`: ``"window"`` when intervals crossing the window boundaries are clipped, else ``"none"``.
    - `censoring`: whether entities still in the system at the end are ``"exclude"``-d or ``"include"``-d.
    - `ema_reset`: whether an exponential moving average restarts at the warm-up.
    - `empty`: the scalar when there is no observation; None leaves the scalar out of the results.
    - `description`: free text.
    """

    name: str
    _: KW_ONLY
    unit: str
    kind: tuple[KpiKind, ...] = ("scalar",)
    observation: str = "job"
    cohort: Cohort = "completed_in_window"
    aggregation: str = "mean"
    clip: Clip = "none"
    censoring: Censoring = "exclude"
    ema_reset: bool = False
    empty: float | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or "/" in self.name or "\0" in self.name:
            raise ValueError(f"KPI name must be a non-empty string without '/' or NUL, got {self.name!r}")
        kind = tuple(self.kind)
        if not kind or len(set(kind)) != len(kind) or not set(kind) <= _KINDS:
            raise ValueError(f"KPI {self.name!r}: kind must name 'series', 'scalar' or both, got {self.kind!r}")
        object.__setattr__(self, "kind", kind)
        for field, value, allowed in (
            ("cohort", self.cohort, _COHORTS),
            ("clip", self.clip, _CLIPS),
            ("censoring", self.censoring, _CENSORING),
        ):
            if value not in allowed:
                raise ValueError(f"KPI {self.name!r}: {field} must be one of {sorted(allowed)}, got {value!r}")
        if self.empty is not None:
            object.__setattr__(self, "empty", float(self.empty))


# ---------------------------------------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Window:
    """The observation window ``[start, end)``, or ``[start, end]`` when `closed`."""

    start: float
    end: float
    closed: bool

    @property
    def length(self) -> float:
        """``end - start``; time-weighted KPIs divide by it."""
        return self.end - self.start

    def __contains__(self, t: float) -> bool:
        return self.start <= t < self.end or (self.closed and t == self.end)


def observation_window(env: Environment) -> Window:
    """The observation window of `env` as of now (global C1.8).

    It starts at the warm-up. After ``run(until=horizon)`` it is ``[warmup, horizon)``, matching SimPy, which
    does not process events scheduled at the horizon; otherwise (exhaustion, a run in progress, no run yet) it
    is ``[warmup, now]``. A run shorter than the warm-up gives the empty window ``[warmup, warmup)``.
    """
    start = env.warmup
    now = float(env.now)
    policy = env._stopping_policy
    horizon = policy.get("horizon") if isinstance(policy, FrozenMap) else None  # only horizon policies have one
    at_horizon = isinstance(horizon, float) and now >= horizon
    return Window(start=start, end=max(start, now), closed=now >= start and not at_horizon)


# ---------------------------------------------------------------------------------------------------------
# Accumulators
# ---------------------------------------------------------------------------------------------------------


class _ExactSum:
    """Running sum, exactly rounded like :func:`math.fsum` (D58), in a few partials instead of every term."""

    __slots__ = ("_partials", "_special")

    def __init__(self) -> None:
        self._partials: list[float] = []
        self._special = 0.0  # sum of the non-finite terms

    def add(self, x: float) -> None:
        if not math.isfinite(x):
            self._special += x
            return
        partials = self._partials
        i = 0
        for y in partials:  # Shewchuk's algorithm, as in math.fsum
            if abs(x) < abs(y):
                x, y = y, x
            hi = x + y
            lo = y - (hi - x)
            if lo:
                partials[i] = lo
                i += 1
            x = hi
        partials[i:] = [x]

    def copy(self) -> _ExactSum:
        twin = _ExactSum()
        twin._partials = list(self._partials)
        twin._special = self._special
        return twin

    def value(self) -> float:
        total = math.fsum(self._partials)
        special = self._special
        return total if special == 0.0 else special + total


class _Aggregate:
    """Aggregation of the observations of one scalar KPI."""

    __slots__ = ("_kind", "cohort", "count", "high", "low", "total")

    def __init__(self, aggregation: str, cohort: Cohort) -> None:
        self._kind = aggregation
        self.cohort = cohort
        self.count = 0
        self.total = _ExactSum()
        self.low = math.inf
        self.high = -math.inf

    def add(self, value: float) -> None:
        self.count += 1
        self.total.add(value)
        self.low = min(self.low, value)
        self.high = max(self.high, value)

    def result(self) -> float | None:
        if not self.count:
            return None
        kind = self._kind
        if kind == "mean":
            return self.total.value() / self.count
        if kind == "sum":
            return self.total.value()
        if kind == "count":
            return float(self.count)
        return self.low if kind == "min" else self.high


class TimeWeighted:
    """Time-weighted mean of a piecewise-constant signal, accumulated in constant memory.

    The signal starts with `value` and changes at each :meth:`update`. Only its part from `start` on is
    accumulated, so `start` is the left edge of the window: create the accumulator with the warm-up (the
    window start of :func:`observation_window`, fixed at activation). Updates before `start` only set the
    value carried into the window. Integrals are exactly rounded sums (D58).
    """

    __slots__ = ("_integral", "_last", "_t", "_value", "start")

    def __init__(self, start: float, value: float = 0.0) -> None:
        self.start = float(start)
        self._t = self.start  # accumulated up to here
        self._last = -math.inf  # time of the last update
        self._value = float(value)
        self._integral = _ExactSum()

    @property
    def value(self) -> float:
        """The current value of the signal."""
        return self._value

    def update(self, t: float, value: float) -> None:
        """The signal takes `value` from time `t` on; `t` must not precede the previous update."""
        if t < self._last:
            raise ValueError(f"update at {t} precedes the previous update at {self._last}")
        self._last = t
        if t > self._t:
            self._integral.add(self._value * (t - self._t))
            self._t = t
        self._value = float(value)

    def mean(self, window_start: float, window_end: float) -> float | None:
        """The mean over ``[window_start, window_end)``, the current value holding after the last update.

        `window_start` must be the accumulator's `start` and `window_end` must not precede the last update.
        Returns None for an empty window. Raises `ValueError` for a window it cannot compute.
        """
        if window_start != self.start:
            raise ValueError(f"window_start {window_start} differs from the accumulator start {self.start}")
        if window_end < window_start:
            raise ValueError(f"window_end {window_end} precedes window_start {window_start}")
        if window_end < self._t:
            raise ValueError(f"window_end {window_end} precedes the last update at {self._t}")
        if window_end == window_start:
            return None
        integral = self._integral.copy()  # the accumulated terms plus the current value up to the window end
        integral.add(self._value * (window_end - self._t))
        return integral.value() / (window_end - window_start)


# ---------------------------------------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------------------------------------


class Collector:
    """Base class of KPI collectors (spec §12.1), bound to an owner entity, its `scope`.

    Subclasses declare :attr:`kpis` and :attr:`subscribes`, implement :meth:`on_event`, record observations
    with :meth:`observe` and series samples with :meth:`sample`, and expose further results as attributes.
    Scalars that are not plain aggregations of observations (time-weighted means, rates) come from
    :meth:`scalar_values`. :meth:`attach` subscribes the collector and registers it with the environment.

    When :attr:`scope_field` names a payload field (``"shopfloor"``, ``"fleet"``, ``"server"``), events whose
    field holds another entity's id are not delivered; events without the field are, and the collector
    filters them itself.
    """

    kpis: ClassVar[tuple[KPI, ...]] = ()
    """The KPIs this collector produces."""
    subscribes: ClassVar[tuple[type[Event], ...] | Literal["*", "**"]] = ()
    """The event classes delivered to :meth:`on_event` (``"*"``: every domain event; ``"**"``: every event)."""
    scope_field: ClassVar[str | None] = None
    """Payload field naming the owner of an event, used to filter events of other scopes."""

    def __init__(self, scope: Entity) -> None:
        if not isinstance(scope, Entity) or getattr(scope, "id", None) is None:
            raise TypeError(f"a collector's scope must be an attached entity, got {scope!r}")
        self.scope = scope
        """The owner entity."""
        self._scope_id: str = scope.id
        self._env: Environment | None = None
        self._subscription: Subscription | None = None
        self._declared: dict[str, KPI] = {}
        self._series: frozenset[str] = frozenset()
        self._keys: frozenset[str] = frozenset()
        self._aggregates: dict[str, _Aggregate] = {}

    @property
    def env(self) -> Environment:
        """The environment the collector is attached to (`RuntimeError` before :meth:`attach`)."""
        env = self._env
        if env is None:
            raise RuntimeError(f"{type(self).__name__} is not attached; call attach(env) first")
        return env

    def attach(self, env: Environment) -> Self:
        """Subscribe to :attr:`subscribes`, register with `env` and return the collector.

        Raises `RuntimeError` when already attached, `ValueError` when the scope is not a live entity of `env`,
        when two KPIs share a name, or when another collector of `env` already produces a KPI of this scope
        with the same name, and `TypeError` for a declaration that is not a :class:`KPI`.
        """
        if self._env is not None:
            raise RuntimeError(f"{type(self).__name__} is already attached")
        if not env.entities.is_live(self.scope):
            raise ValueError(f"scope {self._scope_id!r} is not a live entity of this environment")
        declared: dict[str, KPI] = {}
        for kpi in self.kpis:
            if not isinstance(kpi, KPI):
                raise TypeError(f"{type(self).__name__}.kpis must hold KPI declarations, got {kpi!r}")
            if kpi.name in declared:
                raise ValueError(f"{type(self).__name__} declares the KPI {kpi.name!r} twice")
            declared[kpi.name] = kpi
        keys = frozenset(f"{self._scope_id}/{name}" for name in declared)
        for other in env._collectors:
            clash = keys & other._keys
            if clash:
                raise ValueError(f"KPIs {sorted(clash)} are already produced by {type(other).__name__}")
        self._declared = declared
        self._series = frozenset(kpi.name for kpi in declared.values() if "series" in kpi.kind)
        self._keys = keys
        self._env = env
        if self.subscribes:
            self._subscription = env.bus.subscribe(self._handler(), self.subscribes)
        env._collectors.append(self)
        return self

    def _handler(self) -> Callable[[Event], None]:
        """:meth:`on_event`, behind the owner filter of :attr:`scope_field` when it is set."""
        on_event = self.on_event
        field = self.scope_field
        if field is None:
            return on_event
        scope_id = self._scope_id

        def deliver(event: Event) -> None:
            owner = getattr(event, field, _MISSING)
            if owner is _MISSING or owner == scope_id:
                on_event(event)

        return deliver

    def on_event(self, event: Event) -> None:
        """Handle a subscribed event of this scope; the default ignores it."""

    @property
    def window(self) -> Window:
        """The observation window of the environment as of now (see :func:`observation_window`)."""
        return observation_window(self.env)

    def sample(self, kpi: str, value: float) -> None:
        """Emit a ``kpi.sample`` of the series `kpi` with `value` at the current time.

        Built only when someone listens to :class:`~simulatte.events.KpiSample`. Raises `ValueError` when
        `kpi` is not a declared series.
        """
        env = self.env
        if kpi not in self._series:
            raise ValueError(f"{kpi!r} is not a series KPI of {type(self).__name__}")
        if env.wants(KpiSample):
            env.emit(KpiSample(kpi=kpi, scope=self._scope_id, value=float(value)))

    def observe(self, kpi: str, value: float, *, arrived: float | None = None) -> None:
        """Record an observation of the scalar `kpi` made now, if it belongs to the KPI's cohort.

        ``completed_in_window`` keeps observations made at or after the warm-up, ``arrived_in_window`` those
        whose `arrived` time is at or after it, ``all`` every one. Raises `ValueError` when `kpi` is not a
        declared scalar, when its aggregation is not one :meth:`observe` supports, or when `arrived` is
        missing for an arrival cohort.
        """
        env = self.env
        aggregate = self._aggregates.get(kpi)
        if aggregate is None:
            declaration = self._declared.get(kpi)
            if declaration is None or "scalar" not in declaration.kind:
                raise ValueError(f"{kpi!r} is not a scalar KPI of {type(self).__name__}")
            if declaration.aggregation not in _OBSERVED_AGGREGATIONS:
                raise ValueError(
                    f"KPI {kpi!r}: observe() supports the aggregations {sorted(_OBSERVED_AGGREGATIONS)}, "
                    f"not {declaration.aggregation!r}; compute it in scalar_values()"
                )
            aggregate = self._aggregates[kpi] = _Aggregate(declaration.aggregation, declaration.cohort)
        cohort = aggregate.cohort
        if cohort == "completed_in_window":
            if env.now < env.warmup:
                return
        elif cohort == "arrived_in_window":
            if arrived is None:
                raise ValueError(f"KPI {kpi!r} has an arrival cohort: observe() needs arrived=")
            if arrived < env.warmup:
                return
        aggregate.add(float(value))

    def scalar_values(self) -> Mapping[str, float | None]:
        """Scalars the collector computes itself, by KPI name (None: no observation); the default has none.

        They take precedence over the aggregation of :meth:`observe` for the same KPI.
        """
        return {}

    def scalars(self) -> dict[str, float]:
        """The scalar KPIs keyed ``"<scope id>/<kpi name>"``, in declaration order.

        A KPI without observations takes its `empty` value, and is left out when that is None. Raises
        `ValueError` when :meth:`scalar_values` names a KPI that is not a declared scalar.
        """
        _ = self.env  # raises when not attached
        computed = self.scalar_values()
        declared = self._declared
        unknown = [name for name in computed if name not in declared or "scalar" not in declared[name].kind]
        if unknown:
            raise ValueError(f"{type(self).__name__}.scalar_values() names undeclared scalars {sorted(unknown)}")
        results: dict[str, float] = {}
        for name, kpi in declared.items():
            if "scalar" not in kpi.kind:
                continue
            if name in computed:
                value = computed[name]
            else:
                aggregate = self._aggregates.get(name)
                value = None if aggregate is None else aggregate.result()
            if value is None:
                value = kpi.empty
            if value is not None:
                results[f"{self._scope_id}/{name}"] = float(value)
        return results
