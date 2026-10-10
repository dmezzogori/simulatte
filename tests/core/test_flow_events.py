"""Shop-floor flow events, job retirement and builder prefixes (spec §5.1, §5.2, §6.4)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from simulatte.builders import (
    build_continuous_release_system,
    build_conwip_system,
    build_draco_system,
    build_focus_system,
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_limit_system,
    build_slar_system,
    build_starvation_avoidance_system,
)
from simulatte.entities import EntityRetired
from simulatte.environment import Environment
from simulatte.events import DomainEvent, Event, apply_deltas
from simulatte.job import ProductionJob
from simulatte.kpi import Collector
from simulatte.psp import PreShopPool, PspEntered, PspExited
from simulatte.scenario import Scenario
from simulatte.server import Server, ServerWorkCredited
from simulatte.shopfloor import (
    JobFinished,
    OperationCompleted,
    OperationStarted,
    ShopFloor,
    ShopFloorEntered,
    ShopFloorWipUpdated,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from simulatte.typing import BuiltSystem


FLOW_TYPES = {
    "psp.entered",
    "psp.exited",
    "shopfloor.entered",
    "operation.started",
    "operation.completed",
    "shopfloor.wip_updated",
    "job.finished",
}


class ReplayChecker:
    """Applies the deltas of every domain event and compares the result with the live registry."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.state: dict[str, dict[str, Any]] = {}
        self.checked: list[str] = []
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        apply_deltas(self.state, event.deltas)
        assert self.state == self.env.entities.snapshot(), event
        self.checked.append(event.type_name)


def _record(env: Environment, types: Any = "*") -> list[Event]:
    seen: list[Event] = []
    env.bus.subscribe(seen.append, types)
    return seen


def test_one_operation_phase_sequence() -> None:
    env = Environment(debug=True)
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    env.activate()
    seen = _record(env)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=3)
    sf.add(job)
    env.run()

    assert [e.type_name for e in seen] == [
        "entity.created",
        "shopfloor.entered",
        "job.queued",
        "job.granted",
        "operation.started",
        "server.work_credited",
        "operation.completed",
        "shopfloor.wip_updated",
        "job.released",
        "job.finished",
        "entity.retired",
    ]
    by_type = {e.type_name: e for e in seen}

    entered = by_type["shopfloor.entered"]
    assert isinstance(entered, ShopFloorEntered)
    assert (entered.job, entered.shopfloor, entered.t) == (job.id, sf.id, 0)
    assert entered.deltas.ops == (
        ("set", sf.id, "jobs_in_system", 1),
        ("put", sf.id, "wip", server.id, 5.0),
        ("set", job.id, "shopfloor", sf.id),
        ("set", job.id, "location", "transit"),
    )
    locations = [op[3] for e in seen for op in e.deltas.ops if op[:3] == ("set", job.id, "location")]
    assert locations == ["transit", f"queue:{server.id}", f"server:{server.id}", "transit", "done"]

    started = by_type["operation.started"]
    assert isinstance(started, OperationStarted)
    assert (started.job, started.server, started.op_index) == (job.id, server.id, 0)
    assert (started.processing_time, started.planned_end) == (5.0, 5.0)
    assert started.deltas.ops == (("set", job.id, "op_index", 0),)

    completed = by_type["operation.completed"]
    assert isinstance(completed, OperationCompleted)
    assert (completed.job, completed.server, completed.op_index, completed.processing_time) == (
        job.id,
        server.id,
        0,
        5.0,
    )
    assert completed.t == 5
    assert completed.deltas.ops == ()  # the server credits worked_time itself (ruling R29)
    credited = by_type["server.work_credited"]
    assert isinstance(credited, ServerWorkCredited)
    assert (credited.server, credited.job, credited.processing_time, credited.t) == (server.id, job.id, 5.0, 5)
    assert credited.deltas.ops == (("set", server.id, "worked_time", 5.0),)

    wip = by_type["shopfloor.wip_updated"]
    assert isinstance(wip, ShopFloorWipUpdated)
    assert wip.shopfloor == sf.id
    assert dict(wip.changes) == {server.id: 0.0}
    assert wip.deltas.ops == (("put", sf.id, "wip", server.id, 0.0),)

    finished = by_type["job.finished"]
    assert isinstance(finished, JobFinished)
    assert (finished.job, finished.shopfloor) == (job.id, sf.id)
    assert (finished.makespan, finished.lateness, finished.total_queue_time) == (5.0, 2.0, 0.0)
    assert finished.deltas.ops == (
        ("set", job.id, "location", "done"),
        ("set", job.id, "finished_at", 5.0),
        ("set", sf.id, "jobs_in_system", 0),
    )

    retired = seen[-1]
    assert isinstance(retired, EntityRetired)
    assert (retired.entity, retired.kind) == (job.id, "job")


class _CountedTime(float):
    """A float subclass whose own ``__float__`` counts its calls; event construction must never call it."""

    calls: ClassVar[list[str]] = []

    def __float__(self) -> float:
        _CountedTime.calls.append("__float__")
        return float.__float__(self)


def _counted_time_run(observe: bool, tmp_path: Path) -> tuple[list[str], float, list[Event]]:
    from simulatte.trace import TraceRecorder

    _CountedTime.calls.clear()
    env = Environment(seed=1, debug=True)
    seen: list[Event] = []
    if observe:
        TraceRecorder(env, tmp_path / "counted.simtrace")
        env.bus.subscribe(seen.append, "**")
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(
        env=env, sku="A", servers=[server], processing_times=[_CountedTime(2.5)], due_date=_CountedTime(4.0)
    )
    sf.add(job)
    env.run()
    env.close()
    return list(_CountedTime.calls), env.now, seen


def test_operation_events_read_float_subclasses_without_user_code(tmp_path: Path) -> None:
    """Payloads, deltas and entity snapshots convert user-provided times through the built-in conversions only
    (spec §6.1, §13): observing changes neither the calls the model makes nor the recorded values."""
    plain_calls, plain_end, _ = _counted_time_run(observe=False, tmp_path=tmp_path)
    observed_calls, observed_end, seen = _counted_time_run(observe=True, tmp_path=tmp_path)

    assert observed_calls == plain_calls
    assert observed_end == plain_end == 2.5
    started = next(e for e in seen if isinstance(e, OperationStarted))
    completed = next(e for e in seen if isinstance(e, OperationCompleted))
    values = [started.processing_time, started.planned_end, completed.processing_time]
    assert values == [2.5, 2.5, 2.5] and {type(v) for v in values} == {float}
    created = next(e for e in seen if e.type_name == "entity.created" and e.deltas.ops[0][2] == "job")
    state = created.deltas.ops[0][3]
    assert state["processing_times"] == (2.5,) and type(state["processing_times"][0]) is float
    assert type(state["due_date"]) is float


def test_two_operations_update_op_index_and_location() -> None:
    env = Environment(debug=True)
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf, name="cut")
    s2 = Server(env=env, capacity=1, shopfloor=sf, name="drill")
    replay = ReplayChecker(env)
    replay.state = env.entities.snapshot()
    seen = _record(env, (OperationStarted, OperationCompleted, ShopFloorWipUpdated))

    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 2.0], due_date=100.0)
    sf.add(job)
    env.run(until=4)
    assert env.entities.snapshot()[job.id]["location"] == "server:drill"
    assert env.entities.snapshot()[job.id]["op_index"] == 1
    env.run()

    assert [(e.type_name, getattr(e, "server", None), e.t) for e in seen] == [
        ("operation.started", "cut", 0),
        ("operation.completed", "cut", 3),
        ("shopfloor.wip_updated", None, 3),
        ("operation.started", "drill", 3),
        ("operation.completed", "drill", 5),
        ("shopfloor.wip_updated", None, 5),
    ]
    assert [e.op_index for e in seen if isinstance(e, OperationStarted)] == [0, 1]
    assert [e.planned_end for e in seen if isinstance(e, OperationStarted)] == [3.0, 5.0]
    assert "entity.retired" in replay.checked


def test_location_follows_the_job_through_queues_and_servers() -> None:
    """Ruling R8: a job waiting at its first or a later server reads queue:<server>, not null or the previous server."""
    env = Environment(debug=True)
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf, name="cut")
    s2 = Server(env=env, capacity=1, shopfloor=sf, name="drill")
    replay = ReplayChecker(env)
    replay.state = env.entities.snapshot()
    blocker = ProductionJob(env=env, sku="B", servers=[s1, s2], processing_times=[1.0, 10.0], due_date=100.0)
    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 1.0], due_date=100.0)
    sf.add(blocker)
    sf.add(job)

    def location_at(t: float) -> Any:
        env.run(until=t)
        return replay.state[job.id]["location"]

    assert location_at(0.5) == "queue:cut"  # behind the blocker at its first server
    assert location_at(1.5) == "server:cut"
    assert location_at(3.5) == "queue:drill"  # behind the blocker at its second server
    assert location_at(11.5) == "server:drill"
    env.run()
    assert job.id not in replay.state  # retired after job.finished set its location to "done"
    assert job.snapshot()["location"] == "done"
    assert replay.state == env.entities.snapshot()


def test_psp_release_same_instant_sequence() -> None:
    env = Environment(debug=True)
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    psp.on_arrival(lambda job, pool: pool.release(job))
    replay = ReplayChecker(env)
    replay.state = env.entities.snapshot()
    seen = _record(env)

    def arrive() -> Any:
        yield env.timeout(1.0)
        psp.add(ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0))

    env.process(arrive())
    env.run(until=2)

    assert [(e.type_name, e.t) for e in seen] == [
        ("entity.created", 1),
        ("psp.entered", 1),
        ("psp.exited", 1),
        ("shopfloor.entered", 1),
        ("job.queued", 1),
        ("job.granted", 1),
        ("operation.started", 1),
    ]
    job_id = seen[0].entity  # ty: ignore[unresolved-attribute]
    entered, exited = seen[1], seen[2]
    assert isinstance(entered, PspEntered)
    assert (entered.job, entered.psp, entered.position) == (job_id, psp.id, 0)
    assert entered.deltas.ops == (
        ("insert", psp.id, "jobs", 0, job_id),
        ("set", job_id, "location", f"psp:{psp.id}"),
        ("set", job_id, "shopfloor", sf.id),
    )
    assert isinstance(exited, PspExited)
    assert (exited.job, exited.psp, exited.reason) == (job_id, psp.id, "released")
    assert exited.deltas.ops == (("remove", psp.id, "jobs", job_id), ("set", job_id, "location", "transit"))
    assert replay.checked == [e.type_name for e in seen]


def test_job_retired_after_completion_callbacks() -> None:
    env = Environment(debug=True)
    observed: list[tuple[str, Any, bool, bool]] = []
    seen = _record(env, (JobFinished, EntityRetired))

    class Metrics(Collector):
        subscribes: ClassVar = (JobFinished,)

        def on_event(self, event: Event) -> None:
            assert isinstance(event, JobFinished)
            observed.append(("metrics", env.entities.kind_of(event.job), _has(JobFinished), _has(EntityRetired)))

    def _has(cls: type[Event]) -> bool:
        return any(isinstance(e, cls) for e in seen)

    def callback(job: ProductionJob) -> None:
        observed.append(("callback", env.entities.kind_of(job.id), _has(JobFinished), _has(EntityRetired)))

    sf = ShopFloor(env=env, default_metrics=False, on_job_finished=callback)
    Metrics(sf).attach(env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    waiter: list[Any] = []

    def wait_finished() -> Any:
        finished = yield sf.job_finished_event
        waiter.append((finished, env.entities.kind_of(finished.id)))

    env.process(wait_finished())
    sf.add(job)
    env.run()

    assert observed == [("metrics", "job", True, False), ("callback", "job", True, False)]
    assert [e.type_name for e in seen] == ["job.finished", "entity.retired"]
    assert waiter == [(job, None)]  # a process resumed by signal_job_finished sees the retired job
    assert env.entities.kind_of(job.id) is None
    assert sf.jobs_done == [job]


def test_live_registry_shrinks_python_history_kept() -> None:
    env = Environment(seed=3)
    _, servers, sf, router, _ = build_immediate_release_system(env=env, scenario=Scenario(n_servers=3))
    env.run(until=200)

    live = env.entities.live()
    live_jobs = [e for e in live if isinstance(e, ProductionJob)]
    assert len(sf.jobs_done) > 50
    assert set(live_jobs) == set(sf.jobs)
    assert not any(job in live for job in sf.jobs_done)
    assert all(env.entities.get(job.id) is job for job in sf.jobs_done)  # retired, still referenced
    assert live[: len(servers) + 2] == (sf, *servers, router)


def test_shopfloor_jobs_is_insertion_ordered_dict() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    jobs = [ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0) for _ in range(5)]
    for job in reversed(jobs):
        sf.add(job)

    assert isinstance(sf.jobs, dict)
    assert list(sf.jobs) == list(reversed(jobs))
    assert len(sf.jobs) == 5 and jobs[0] in sf.jobs
    env.run(until=1.5)
    assert list(sf.jobs) == list(reversed(jobs))[1:]


def test_job_created_after_activation_gets_owner_on_entry() -> None:
    env = Environment(debug=True)
    sf = ShopFloor(env=env, name="main")
    other = ShopFloor(env=env, name="other")
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    replay = ReplayChecker(env)
    env.activate()
    replay.state = env.entities.snapshot()

    pooled = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    direct = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    assert replay.state[pooled.id]["shopfloor"] is None
    assert replay.state[pooled.id]["location"] is None

    psp.add(pooled)
    assert replay.state[pooled.id]["shopfloor"] == "main"
    assert replay.state[pooled.id]["location"] == f"psp:{psp.id}"

    other.add(direct)
    assert replay.state[direct.id]["shopfloor"] == "other"

    # Entering a different shop floor later overwrites the owner.
    psp.remove(job=pooled)
    assert replay.state[pooled.id]["location"] is None
    other.add(pooled)
    assert replay.state[pooled.id]["shopfloor"] == "other"
    assert replay.state[pooled.id]["location"] == "transit"
    env.run()
    assert replay.state == env.entities.snapshot()


# ---------------------------------------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------------------------------------

BUILDERS: dict[str, Callable[..., BuiltSystem[Any]]] = {
    "immediate": build_immediate_release_system,
    "focus": build_focus_system,
    "lumscor": lambda **kw: build_lumscor_system(check_timeout=5.0, wl_norm_level=6.0, allowance_factor=2, **kw),
    "slar": lambda **kw: build_slar_system(allowance_factor=2.0, **kw),
    "slar_limit": lambda **kw: build_slar_limit_system(allowance_factor=2.0, wl_norm_level=6.0, **kw),
    "draco": lambda **kw: build_draco_system(wip_target=8, loop_target=4, **kw),
    "conwip": lambda **kw: build_conwip_system(wip_cap=6, **kw),
    "continuous": lambda **kw: build_continuous_release_system(wl_norm_level=6.0, **kw),
    "starvation": build_starvation_avoidance_system,
}


def test_two_builders_share_env_with_prefixes() -> None:
    env = Environment(seed=5, debug=True)
    entered = _record(env, (ShopFloorEntered, PspEntered))
    a = build_immediate_release_system(env=env, prefix="a.", scenario=Scenario(n_servers=2))
    b = build_lumscor_system(
        env=env, prefix="b.", scenario=Scenario(n_servers=3), check_timeout=5.0, wl_norm_level=6.0, allowance_factor=2
    )

    assert [s.id for s in a.servers] == ["a.wc-0", "a.wc-1"]
    assert (a.shop_floor.id, a.router.id) == ("a.shopfloor", "a.router")
    assert [s.id for s in b.servers] == ["b.wc-0", "b.wc-1", "b.wc-2"]
    assert b.psp is not None
    assert (b.shop_floor.id, b.router.id, b.psp.id) == ("b.shopfloor", "b.router", "b.psp")
    with pytest.raises(ValueError, match="already used"):
        build_immediate_release_system(env=env, prefix="a.")

    env.run(until=100)

    assert a.shop_floor.jobs_done and b.shop_floor.jobs_done
    owners = {"a.shopfloor": set(a.servers), "b.shopfloor": set(b.servers)}
    floors = [e for e in entered if isinstance(e, ShopFloorEntered)]
    assert {e.shopfloor for e in floors} == set(owners)
    for event in floors:
        job = env.entities.get(event.job)
        assert isinstance(job, ProductionJob)
        assert set(job.servers) <= owners[event.shopfloor]
    assert {e.psp for e in entered if isinstance(e, PspEntered)} == {"b.psp"}


@pytest.mark.parametrize("name", list(BUILDERS))
def test_builder_default_ids_and_prefix(name: str) -> None:
    built = BUILDERS[name](env=Environment(), scenario=Scenario(n_servers=2))
    assert [s.id for s in built.servers] == ["wc-0", "wc-1"]
    assert (built.shop_floor.id, built.router.id) == ("shopfloor", "router")
    assert built.psp is None or built.psp.id == "psp"

    built = BUILDERS[name](env=Environment(), prefix="x-", scenario=Scenario(n_servers=2))
    assert [s.id for s in built.servers] == ["x-wc-0", "x-wc-1"]
    assert (built.shop_floor.id, built.router.id) == ("x-shopfloor", "x-router")
    assert built.psp is None or built.psp.id == "x-psp"


@pytest.mark.parametrize("name", list(BUILDERS))
def test_builder_default_scenario_is_fresh(name: str) -> None:
    first = BUILDERS[name](env=Environment())
    second = BUILDERS[name](env=Environment())
    assert len(first.servers) == len(second.servers) == Scenario().n_servers
    assert first.router.sku_distributions == second.router.sku_distributions


@pytest.mark.parametrize("name", list(BUILDERS))
def test_replay_equals_live_at_every_event_shop(name: str) -> None:
    """The reference shop: every builder, six servers, debug mode, replay checked after each event."""
    env = Environment(seed=11, debug=True)
    replay = ReplayChecker(env)  # subscribed before any entity exists: replay starts from {}
    built = BUILDERS[name](env=env)
    env.run(until=150)

    assert replay.state == env.entities.snapshot()
    seen = set(replay.checked)
    assert FLOW_TYPES - {"psp.entered", "psp.exited"} <= seen
    if built.psp is not None:
        assert {"psp.entered", "psp.exited"} <= seen
    assert replay.checked.count("entity.retired") == len(built.shop_floor.jobs_done) > 20
    assert replay.checked.count("job.finished") == len(built.shop_floor.jobs_done)
    assert replay.checked.count("entity.created") == len(env.entities.live()) + len(built.shop_floor.jobs_done)


def test_flow_events_not_built_without_subscribers() -> None:
    """Behind env.wants: a run with no subscriber still updates the job state the snapshot reports."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    held = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0)
    psp.add(held)
    assert env.entities.snapshot()[held.id]["location"] == f"psp:{psp.id}"
    psp.add(job)
    psp.release(job)
    env.run(until=1)
    state = env.entities.snapshot()[job.id]
    assert (state["shopfloor"], state["location"], state["op_index"]) == (sf.id, f"server:{server.id}", 0)
    floor = env.entities.snapshot()[sf.id]
    assert (floor["wip"], floor["jobs_in_system"]) == ({server.id: 2.0}, 1)
    env.run()
    assert env.entities.kind_of(job.id) is None
    assert job.finished_at == 2.0
    assert env.entities.kind_of(held.id) == "job"


def test_wip_strategy_removing_an_entry_emits_delete() -> None:
    class DropOnCompletion:
        def add_job(self, job: ProductionJob, wip: dict[Server, float]) -> None:
            for server, processing_time in job.server_processing_times:
                wip[server] = wip.get(server, 0.0) + processing_time

        def complete_operation(
            self, job: ProductionJob, server: Server, op_index: int, processing_time: float, wip: dict[Server, float]
        ) -> None:
            del wip[server]

    env = Environment(debug=True)
    sf = ShopFloor(env=env, wip_strategy=DropOnCompletion())
    server = Server(env=env, capacity=1, shopfloor=sf)
    replay = ReplayChecker(env)
    replay.state = env.entities.snapshot()
    seen = _record(env, (ShopFloorWipUpdated,))
    sf.add(ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=5.0))
    env.run()

    assert len(seen) == 1
    assert isinstance(seen[0], ShopFloorWipUpdated)
    assert dict(seen[0].changes) == {}
    assert seen[0].deltas.ops == (("delete", sf.id, "wip", server.id),)
    assert replay.state[sf.id]["wip"] == {}
