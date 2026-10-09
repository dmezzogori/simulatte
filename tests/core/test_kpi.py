"""KPI declarations, scoped collectors, observation windows and their recording (spec §12, global C1.8)."""

from __future__ import annotations

import math
import struct
from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

import pytest

from simulatte._wire import unpack
from simulatte.digest import Fingerprint
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.events import Deltas, DomainEvent, Event, KpiSample, event_type
from simulatte.kpi import KPI, Collector, TimeWeighted, Window, _ExactSum, observation_window
from simulatte.trace import ChunkLimits, RecordType, Trace, TraceCorrupted, TraceRecorder
from simulatte.trace.format import PREAMBLE, RECORD_HEADER, TRAILER, TRAILER_MAGIC
from simulatte.trace.reader import Cursor

# ---------------------------------------------------------------------------------------------------------
# Model: desks complete jobs; each completion carries the job's arrival time
# ---------------------------------------------------------------------------------------------------------


class Desk(Entity, kind="test_kpi_desk"):
    state_schema: ClassVar[StateSchema] = StateSchema({"done": FieldSpec("int")})

    def __init__(self, env: Environment, *, name: str | None = None) -> None:
        self.env = env
        self.done = 0
        env.entities.attach(self, name=name)


@event_type("test.kpi_done", touches={"test_kpi_desk": ("done",)})
class Done(DomainEvent):
    desk: str
    arrived: float


def _finish(env: Environment, desk: Desk, arrived: float) -> None:
    desk.done += 1
    if env.wants(Done):
        env.emit(Done(desk=desk.id, arrived=arrived, deltas=Deltas.build().set(desk.id, "done", desk.done).done()))


def _schedule(env: Environment, desk: Desk, jobs: list[tuple[float, float]]) -> None:
    """Complete each ``(arrived, completed)`` job of `jobs` at its completion time."""

    def process() -> Generator[Any, Any, None]:
        for arrived, completed in jobs:
            yield env.timeout(completed - env.now)
            _finish(env, desk, arrived)

    env.process(process())


class FlowCollector(Collector):
    """Flow time of completed jobs, by completion and by arrival cohort, plus a count of completions."""

    kpis: ClassVar[tuple[KPI, ...]] = (
        KPI("flow_time", unit="time", kind=("series", "scalar")),
        KPI("flow_time_by_arrival", unit="time", cohort="arrived_in_window"),
        KPI("completed", unit="jobs", aggregation="count", empty=0.0),
    )
    subscribes: ClassVar[tuple[type[Event], ...]] = (Done,)
    scope_field: ClassVar[str | None] = "desk"

    def __init__(self, desk: Desk) -> None:
        super().__init__(desk)
        self.flow_times: list[float] = []

    def on_event(self, event: Event) -> None:
        assert isinstance(event, Done)
        flow = event.t - event.arrived
        self.flow_times.append(flow)
        self.observe("flow_time", flow)
        self.observe("flow_time_by_arrival", flow, arrived=event.arrived)
        self.observe("completed", 1.0)
        self.sample("flow_time", flow)


JOBS = [(0.0, 2.0), (1.0, 4.0), (2.0, 6.0), (7.0, 9.0), (8.0, 12.0)]
"""(arrived, completed): flow times 2, 3, 4, 2 inside a horizon of 10; the last job completes after it."""


def _desk_system(
    env: Environment, *, name: str = "desk", jobs: list[tuple[float, float]] = JOBS
) -> tuple[Desk, FlowCollector]:
    desk = Desk(env, name=name)
    collector = FlowCollector(desk).attach(env)
    _schedule(env, desk, jobs)
    return desk, collector


class Samples:
    """Every ``kpi.sample`` of a run, as ``(key, cursor, value)``."""

    def __init__(self, env: Environment) -> None:
        self.seen: list[tuple[str, Cursor, float]] = []
        env.bus.subscribe(self, (KpiSample,))

    def __call__(self, event: KpiSample) -> None:
        self.seen.append((f"{event.scope}/{event.kpi}", (float(event.t), event.seq), event.value))

    def series(self) -> dict[str, list[tuple[Cursor, float]]]:
        out: dict[str, list[tuple[Cursor, float]]] = {}
        for key, cursor, value in self.seen:
            out.setdefault(key, []).append((cursor, value))
        return out


# ---------------------------------------------------------------------------------------------------------
# Required cases
# ---------------------------------------------------------------------------------------------------------


def test_time_weighted_clipping() -> None:
    # Value 2 on [0, 5), 4 on [5, 10); the window [3, 10) holds 2 * 2 + 4 * 5 = 24 over 7 time units (S24).
    tw = TimeWeighted(3.0)
    tw.update(0.0, 2.0)
    tw.update(5.0, 4.0)
    assert tw.mean(3.0, 10.0) == 24 / 7

    # Boundary cases, each computed by hand.
    ends_at_change = TimeWeighted(3.0)
    ends_at_change.update(0.0, 2.0)
    ends_at_change.update(5.0, 4.0)
    assert ends_at_change.mean(3.0, 5.0) == 2.0  # [3, 5) at value 2; the change at 5 has zero weight
    at_start = TimeWeighted(5.0)
    at_start.update(5.0, 1.0)
    at_start.update(7.0, 3.0)
    assert at_start.mean(5.0, 9.0) == 2.0  # (1 * 2 + 3 * 2) / 4
    same_time = TimeWeighted(0.0)
    same_time.update(1.0, 4.0)
    same_time.update(1.0, 6.0)  # the last value at a time holds
    assert same_time.mean(0.0, 2.0) == 3.0  # (0 * 1 + 6 * 1) / 2
    assert TimeWeighted(0.0).mean(0.0, 4.0) == 0.0
    assert TimeWeighted(0.0, value=1.5).mean(0.0, 4.0) == 1.5
    assert TimeWeighted(3.0).mean(3.0, 3.0) is None  # an empty window has no observation


def test_completion_cohort_excludes_warmup_completions() -> None:
    env = Environment(seed=1)
    env.configure_kpis(warmup=5.0)
    _, collector = _desk_system(env)
    env.run(until=10)

    # Completed in [5, 10): the jobs completing at 6 (flow 4) and 9 (flow 2); 2 and 4 are warm-up completions.
    assert collector.flow_times == [2.0, 3.0, 4.0, 2.0]
    assert collector.scalars() == {
        "desk/flow_time": 3.0,
        "desk/flow_time_by_arrival": 2.0,  # only the job arriving at 7 arrived in the window and completed
        "desk/completed": 2.0,
    }
    assert env.manifest().requested["warmup"] == 5.0

    no_warmup = Environment(seed=1)
    _, everything = _desk_system(no_warmup)
    no_warmup.run(until=10)
    assert everything.scalars() == {
        "desk/flow_time": 2.75,
        "desk/flow_time_by_arrival": 2.75,
        "desk/completed": 4.0,
    }

    late = Environment(seed=1)
    late.configure_kpis(warmup=50.0)
    _, nothing = _desk_system(late)
    late.run(until=10)
    assert nothing.scalars() == {"desk/completed": 0.0}  # no observation: `empty`, or left out without one


def test_kpi_samples_not_in_digest() -> None:
    def run(with_collector: bool) -> tuple[Fingerprint, Samples, Environment]:
        env = Environment(seed=1)
        env.enable_digest()
        samples = Samples(env)
        desk = Desk(env, name="desk")
        if with_collector:
            FlowCollector(desk).attach(env)
        _schedule(env, desk, JOBS)
        env.run(until=10)
        return env.fingerprint(), samples, env

    plain, no_samples, _ = run(with_collector=False)
    observed, samples, env = run(with_collector=True)
    assert no_samples.seen == []
    assert [value for _, _, value in samples.seen] == [2.0, 3.0, 4.0, 2.0]
    assert observed.digest == plain.digest  # samples are observer events: outside the projection
    assert plain.kpis == {}
    assert observed.kpis == {"desk/flow_time": 2.75, "desk/flow_time_by_arrival": 2.75, "desk/completed": 4.0}


def test_scalars_namespaced_by_scope() -> None:
    env = Environment(seed=1)
    _, a = _desk_system(env, name="a", jobs=[(0.0, 1.0), (0.0, 3.0)])
    _, b = _desk_system(env, name="b", jobs=[(0.0, 5.0)])
    env.run()

    assert a.flow_times == [1.0, 3.0]  # each collector sees only its own desk's completions
    assert b.flow_times == [5.0]
    assert a.scalars() == {"a/flow_time": 2.0, "a/flow_time_by_arrival": 2.0, "a/completed": 2.0}
    assert env.fingerprint().kpis == {
        "a/completed": 2.0,
        "a/flow_time": 2.0,
        "a/flow_time_by_arrival": 2.0,
        "b/completed": 1.0,
        "b/flow_time": 5.0,
        "b/flow_time_by_arrival": 5.0,
    }


@pytest.mark.parametrize("level", ["full", "kpi"])
def test_recorder_stores_kpis(tmp_path: Path, level: Literal["full", "kpi"]) -> None:
    path = tmp_path / f"{level}.simtrace"
    env = Environment(seed=1)
    samples = Samples(env)
    TraceRecorder(env, path, level=level, chunk_limits=ChunkLimits(max_events=2))
    jobs = [(float(i), 2.0 * i + 1.0) for i in range(12)]  # flow times 1 to 12
    _desk_system(env, name="a", jobs=jobs)
    _desk_system(env, name="b", jobs=jobs[:5])
    env.run()
    env.close()

    trace = Trace.open(path)
    assert trace.kpis() == env.fingerprint().kpis
    assert trace.fingerprint == env.fingerprint()
    assert set(trace.kpis()) == {f"{s}/{k}" for s in "ab" for k in ("flow_time", "flow_time_by_arrival", "completed")}
    assert trace.kpi_series() == samples.series()
    assert len(trace.kpi_series()["a/flow_time"]) == 12
    assert trace.verify() == (True if level == "full" else "not_verifiable")


# ---------------------------------------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------------------------------------


def test_kpi_declaration_defaults_and_validation() -> None:
    kpi = KPI("flow_time", unit="time", kind=["series", "scalar"], empty=0)  # ty: ignore[invalid-argument-type]
    assert kpi.kind == ("series", "scalar")
    assert kpi.empty == 0.0 and isinstance(kpi.empty, float)
    assert (kpi.observation, kpi.cohort, kpi.aggregation, kpi.clip, kpi.censoring, kpi.ema_reset) == (
        "job",
        "completed_in_window",
        "mean",
        "none",
        "exclude",
        False,
    )
    assert KPI("wip", unit="jobs").kind == ("scalar",)
    for name in ("", "a/b", "a\0b"):
        with pytest.raises(ValueError, match="name"):
            KPI(name, unit="time")
    for kind in ((), ("scalar", "scalar"), ("histogram",), "scalar"):
        with pytest.raises(ValueError, match="kind"):
            KPI("x", unit="time", kind=kind)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="cohort"):
        KPI("x", unit="time", cohort="started")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="clip"):
        KPI("x", unit="time", clip="both")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="censoring"):
        KPI("x", unit="time", censoring="partial")  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------------------------------------
# Windows and warm-up
# ---------------------------------------------------------------------------------------------------------


def test_window_follows_the_stopping_policy() -> None:
    env = Environment(seed=1)
    env.configure_kpis(warmup=2.0)
    desk, collector = _desk_system(env, jobs=[(0.0, 1.0), (0.0, 7.0)])
    assert collector.window == Window(start=2.0, end=2.0, closed=False)  # nothing ran yet: empty

    env.run(until=5)
    window = collector.window
    assert window == Window(start=2.0, end=5.0, closed=False)  # [warmup, horizon)
    assert window.length == 3.0
    assert 2.0 in window and 4.999 in window and 5.0 not in window and 1.999 not in window

    env.run()  # to exhaustion: the last event, at 7, is inside
    assert collector.window == Window(start=2.0, end=7.0, closed=True)
    assert 7.0 in collector.window

    short = Environment(seed=1)
    short.configure_kpis(warmup=10.0)
    short.run(until=4)
    assert observation_window(short) == Window(start=10.0, end=10.0, closed=False)  # shorter than the warm-up
    assert 10.0 not in observation_window(short)

    stopped = Environment(seed=1)
    stopped.run(until=stopped.timeout(3))  # stopped by an event: [0, now]
    assert observation_window(stopped) == Window(start=0.0, end=3.0, closed=True)
    assert desk.done == 2


def test_window_during_a_horizon_run_ends_now() -> None:
    env = Environment(seed=1)
    windows: list[Window] = []

    def probe() -> Generator[Any, Any, None]:
        yield env.timeout(4)
        windows.append(observation_window(env))

    env.process(probe())
    env.run(until=10)
    assert windows == [Window(start=0.0, end=4.0, closed=True)]


def test_configure_kpis_before_activation_only(tmp_path: Path) -> None:
    env = Environment(seed=1)
    assert env.warmup == 0.0
    env.configure_kpis(warmup=3)
    assert env.warmup == 3.0
    assert env.manifest().requested["warmup"] == 3.0
    for bad in (-1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="warmup"):
            env.configure_kpis(warmup=bad)
    env.activate()
    with pytest.raises(RuntimeError, match="before activation"):
        env.configure_kpis(warmup=1.0)
    assert env.manifest().requested["warmup"] == 3.0

    path = tmp_path / "warm.simtrace"
    recorded = Environment(seed=1)
    TraceRecorder(recorded, path)
    recorded.configure_kpis(warmup=4.5)  # after attachment, before activation: lands in the INITIAL record
    recorded.run(until=1)
    recorded.close()
    assert Trace.open(path).manifest["warmup"] == 4.5


# ---------------------------------------------------------------------------------------------------------
# Accumulators
# ---------------------------------------------------------------------------------------------------------


def test_time_weighted_rejects_windows_it_cannot_compute() -> None:
    tw = TimeWeighted(2.0, value=1.0)
    assert tw.value == 1.0
    tw.update(1.0, 3.0)  # before start: only the value carried into the window
    tw.update(4.0, 5.0)
    assert tw.value == 5.0
    with pytest.raises(ValueError, match="precedes the previous update"):
        tw.update(3.0, 1.0)
    with pytest.raises(ValueError, match="differs from the accumulator start"):
        tw.mean(0.0, 6.0)
    with pytest.raises(ValueError, match="precedes window_start"):
        tw.mean(2.0, 1.0)
    with pytest.raises(ValueError, match="precedes the last update"):
        tw.mean(2.0, 3.0)
    assert tw.mean(2.0, 6.0) == 4.0  # (3 * 2 + 5 * 2) / 4

    before = TimeWeighted(5.0)
    before.update(1.0, 2.0)
    with pytest.raises(ValueError, match="precedes the previous update"):
        before.update(0.5, 2.0)  # ordering is checked before the start too


def test_time_weighted_sums_are_exactly_rounded() -> None:
    tw = TimeWeighted(0.0)
    for i in range(10):
        tw.update(float(i), 0.1)
    assert tw.mean(0.0, 10.0) == 0.1  # sequential addition would give 0.09999999999999999
    total = 0.0
    for _ in range(10):
        total += 0.1
    assert total / 10 != 0.1


def test_exact_sum_non_finite_terms() -> None:
    def summed(*terms: float) -> float:
        total = _ExactSum()
        for term in terms:
            total.add(term)
        return total.value()

    assert summed() == 0.0
    assert summed(1e100, 1.0, -1e100) == 1.0
    assert summed(1.0, math.inf, 2.0) == math.inf
    assert summed(-math.inf, 1.0) == -math.inf
    assert math.isnan(summed(math.inf, -math.inf))
    assert math.isnan(summed(1.0, math.nan))


# ---------------------------------------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------------------------------------


class Plain(Collector):
    """Every aggregation that observe() supports, with the "all" cohort, and no subscription."""

    kpis: ClassVar[tuple[KPI, ...]] = tuple(
        KPI(aggregation, unit="x", cohort="all", aggregation=aggregation)
        for aggregation in ("mean", "sum", "count", "min", "max")
    ) + (
        KPI("level", unit="x", kind=("series",)),
        KPI("ratio", unit="x", aggregation="ratio"),
        KPI("unset", unit="x"),
    )


def test_observe_aggregations_and_errors() -> None:
    env = Environment(seed=1)
    env.configure_kpis(warmup=100.0)  # the "all" cohort ignores the window
    collector = Plain(Desk(env, name="d")).attach(env)
    assert collector._subscription is None
    for value in (0.1, 0.2, 0.3, -1.0):
        for name in ("mean", "sum", "count", "min", "max"):
            collector.observe(name, value)
    assert collector.scalars() == {
        "d/mean": math.fsum([0.1, 0.2, 0.3, -1.0]) / 4,
        "d/sum": math.fsum([0.1, 0.2, 0.3, -1.0]),
        "d/count": 4.0,
        "d/min": -1.0,
        "d/max": 0.3,
    }  # "ratio" and "unset" have no value and no `empty`: left out
    with pytest.raises(ValueError, match="not a scalar KPI"):
        collector.observe("level", 1.0)
    with pytest.raises(ValueError, match="not a scalar KPI"):
        collector.observe("nope", 1.0)
    with pytest.raises(ValueError, match="supports the aggregations"):
        collector.observe("ratio", 1.0)
    with pytest.raises(ValueError, match="not a series KPI"):
        collector.sample("mean", 1.0)
    with pytest.raises(ValueError, match="not a series KPI"):
        collector.sample("nope", 1.0)
    collector.sample("level", 1.0)  # nobody listens: no event is built

    arrival = FlowCollector(Desk(env, name="e")).attach(env)
    with pytest.raises(ValueError, match="arrived="):
        arrival.observe("flow_time_by_arrival", 1.0)


def test_scalar_values_take_precedence() -> None:
    class Computed(Plain):
        def __init__(self, scope: Entity, values: Mapping[str, float | None]) -> None:
            super().__init__(scope)
            self.values = values

        def scalar_values(self) -> Mapping[str, float | None]:
            return self.values

    env = Environment(seed=1)
    desk = Desk(env, name="d")
    computed = Computed(desk, {"ratio": 0.5, "mean": None}).attach(env)
    computed.observe("mean", 3.0)  # overridden by scalar_values: None, and no `empty`
    assert computed.scalars() == {"d/ratio": 0.5}
    for bad in ({"level": 1.0}, {"nope": 1.0}):
        computed.values = bad
        with pytest.raises(ValueError, match="undeclared scalars"):
            computed.scalars()


def test_attach_checks_scope_declarations_and_clashes() -> None:
    env = Environment(seed=1)
    desk = Desk(env, name="d")
    with pytest.raises(TypeError, match="attached entity"):
        Plain(object())  # ty: ignore[invalid-argument-type]

    class Unattached(Entity, kind="test_kpi_unattached"):
        state_schema: ClassVar[StateSchema] = StateSchema({})

    with pytest.raises(TypeError, match="attached entity"):
        Plain(Unattached())
    collector = Plain(desk)
    with pytest.raises(RuntimeError, match="not attached"):
        collector.scalars()
    with pytest.raises(RuntimeError, match="not attached"):
        _ = collector.window
    with pytest.raises(ValueError, match="not a live entity"):
        collector.attach(Environment(seed=1))
    assert collector.attach(env) is collector
    with pytest.raises(RuntimeError, match="already attached"):
        collector.attach(env)
    with pytest.raises(ValueError, match="already produced by Plain"):
        Plain(desk).attach(env)  # the same KPIs for the same scope
    Plain(Desk(env, name="other")).attach(env)  # another scope: no clash

    class Twice(Collector):
        kpis: ClassVar[tuple[KPI, ...]] = (KPI("x", unit="x"), KPI("x", unit="y"))

    class NotKpi(Collector):
        kpis: ClassVar[tuple[KPI, ...]] = ("x",)  # ty: ignore[invalid-assignment]

    with pytest.raises(ValueError, match="twice"):
        Twice(desk).attach(env)
    with pytest.raises(TypeError, match="KPI declarations"):
        NotKpi(desk).attach(env)
    assert len(env._collectors) == 2


def test_scope_filter_and_unfiltered_delivery() -> None:
    @event_type("test.kpi_ping")
    class Ping(DomainEvent):
        n: int

    class Everything(Collector):
        subscribes: ClassVar[Literal["*"]] = "*"

        def __init__(self, scope: Entity) -> None:
            super().__init__(scope)
            self.seen: list[str] = []

        def on_event(self, event: Event) -> None:
            self.seen.append(type(event).type_name)

    class Filtered(Everything):
        scope_field: ClassVar[str | None] = "desk"

    class Ignoring(Collector):
        subscribes: ClassVar[tuple[type[Event], ...]] = (Done,)

    env = Environment(seed=1)
    a = Desk(env, name="a")
    b = Desk(env, name="b")
    everything = Everything(a).attach(env)
    filtered = Filtered(a).attach(env)
    Ignoring(a).attach(env)  # the default on_event ignores events
    env.activate()
    _finish(env, a, 0.0)
    _finish(env, b, 0.0)
    env.emit(Ping(n=1))  # no desk field: delivered to the filtered collector too
    assert everything.seen == ["test.kpi_done", "test.kpi_done", "test.kpi_ping"]
    assert filtered.seen == ["test.kpi_done", "test.kpi_ping"]


def test_collectors_in_debug_mode() -> None:
    env = Environment(seed=1, debug=True)
    samples = Samples(env)
    _desk_system(env)
    env.run(until=10)
    assert [value for _, _, value in samples.seen] == [2.0, 3.0, 4.0, 2.0]
    assert all(cursor[1] >= 0 for _, cursor, _ in samples.seen)


# ---------------------------------------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------------------------------------


def _records(path: Path) -> list[tuple[int, Any]]:
    """(record type, decoded payload; None for chunks) of every record of a trace file."""
    data = path.read_bytes()
    end = len(data) - TRAILER.size if data.endswith(TRAILER_MAGIC) else len(data)
    out: list[tuple[int, Any]] = []
    pos = PREAMBLE.size
    while pos < end:
        length, rtype, _ = RECORD_HEADER.unpack_from(data, pos)
        payload = data[pos + RECORD_HEADER.size : pos + RECORD_HEADER.size + length]
        out.append((rtype, None if rtype == RecordType.CHUNK else unpack(payload)))
        pos += RECORD_HEADER.size + length
    return out


def test_kpi_records_follow_the_initial_record(tmp_path: Path) -> None:
    path = tmp_path / "prelude.simtrace"
    env = Environment(seed=1)
    TraceRecorder(env, path, level="kpi", chunk_limits=ChunkLimits(max_events=2))
    collector = Plain(Desk(env, name="d")).attach(env)  # entity.created takes seq 0
    for value in (1.0, 2.0, 3.0):
        collector.sample("level", value)  # before activation: held back, beyond the limit
    env.activate()
    collector.sample("level", 4.0)  # published with the earlier ones
    collector.sample("level", 5.0)  # published by close()
    env.close()

    records = _records(path)
    assert [rtype for rtype, _ in records] == [
        RecordType.HEADER,
        RecordType.INITIAL,
        RecordType.KPI,
        RecordType.KPI,
        RecordType.FOOTER,  # no scalar has a value: no scalars record
    ]
    assert records[2][1] == {"samples": tuple((seq, 0.0, "d/level", float(seq)) for seq in range(1, 5))}
    assert records[3][1] == {"samples": ((5, 0.0, "d/level", 5.0),)}
    assert Trace.open(path).kpi_series() == {"d/level": [((0.0, seq), float(seq)) for seq in range(1, 6)]}


def test_kpi_samples_flush_on_the_byte_limit(tmp_path: Path) -> None:
    path = tmp_path / "bytes.simtrace"
    env = Environment(seed=1)
    TraceRecorder(env, path, level="kpi", chunk_limits=ChunkLimits(max_bytes=40))
    collector = Plain(Desk(env, name="d")).attach(env)
    env.activate()
    for value in (1.0, 2.0, 3.0):
        collector.sample("level", value)  # each entry encodes to 21 bytes: two fill a record
    env.close()
    kpis = [body for rtype, body in _records(path) if rtype == RecordType.KPI]
    assert [len(body["samples"]) for body in kpis] == [2, 1]


def test_kpi_series_between_chunks(tmp_path: Path) -> None:
    path = tmp_path / "mid.simtrace"
    env = Environment(seed=1)
    samples = Samples(env)
    TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=2))
    _desk_system(env, jobs=[(0.0, float(t)) for t in range(1, 9)])
    env.run()
    env.close()
    types = [rtype for rtype, _ in _records(path)]
    first_kpi = types.index(RecordType.KPI)
    assert RecordType.CHUNK in types[first_kpi:]  # a KPI record precedes later chunks

    trace = Trace.open(path)
    assert trace.kpi_series() == samples.series()
    series = trace.kpi_series()
    series["desk/flow_time"].clear()  # a copy
    assert trace.kpi_series() == samples.series()

    data = path.read_bytes()
    offset = sum(RECORD_HEADER.size + len_ for len_ in _lengths(data)[:first_kpi]) + PREAMBLE.size
    flipped = data[: offset + RECORD_HEADER.size] + bytes([data[offset + RECORD_HEADER.size] ^ 0xFF])
    flipped += data[offset + RECORD_HEADER.size + 1 :]
    damaged = tmp_path / "crc.simtrace"
    damaged.write_bytes(flipped)
    with pytest.raises(TraceCorrupted, match="KPI record at offset .* fails its CRC check"):
        Trace.open(damaged)

    length = _lengths(data)[first_kpi]
    longer = data[:offset] + struct.pack(">I", length + 10_000) + data[offset + 4 :]
    cut = tmp_path / "cut.simtrace"
    cut.write_bytes(longer)
    with pytest.raises(TraceCorrupted, match="cut short"):
        Trace.open(cut)

    truncated = tmp_path / "nofooter.simtrace"  # no trailer: every record is scanned
    truncated.write_bytes(data[: struct.unpack(">Q", data[-TRAILER.size : -8])[0]])
    assert Trace.open(truncated).kpi_series() == samples.series()


def _lengths(data: bytes) -> list[int]:
    """Payload length of every record of a complete trace."""
    end = len(data) - TRAILER.size
    out: list[int] = []
    pos = PREAMBLE.size
    while pos < end:
        length = RECORD_HEADER.unpack_from(data, pos)[0]
        out.append(length)
        pos += RECORD_HEADER.size + length
    return out
