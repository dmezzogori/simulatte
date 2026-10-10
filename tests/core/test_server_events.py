"""Server resource events: job.queued, job.granted, job.queue_left, job.released, server.queue_reordered."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import simpy

from simulatte.environment import Environment
from simulatte.events import DomainEvent, Event, apply_deltas
from simulatte.job import ProductionJob
from simulatte.server import (
    JobGranted,
    JobQueued,
    JobQueueLeft,
    JobReleased,
    Server,
    ServerQueueReordered,
)
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:
    from simulatte.typing import ProcessGenerator

SERVER_EVENTS = (JobQueued, JobGranted, JobQueueLeft, JobReleased, ServerQueueReordered)


def _job(env: Environment, server: Server, **kwargs: Any) -> ProductionJob:
    return ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0, **kwargs)


def _record(env: Environment, types: Any = SERVER_EVENTS) -> list[Event]:
    seen: list[Event] = []
    env.bus.subscribe(seen.append, types)
    return seen


def _hold(env: Environment, server: Server, job: ProductionJob, duration: float) -> ProcessGenerator:
    with server.request(job=job) as request:
        yield request
        yield env.timeout(duration)


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


@pytest.mark.parametrize("capacity", [1, 2])
def test_queue_length_includes_newcomer(capacity: int) -> None:
    env = Environment(debug=True)
    server = Server(env=env, capacity=capacity)
    seen = _record(env, (JobQueued,))
    for _ in range(capacity + 2):
        env.process(_hold(env, server, _job(env, server), 5.0))
    env.run()

    assert [e.queue_length for e in seen if isinstance(e, JobQueued)] == [1] * capacity + [1, 2]


def test_immediate_grant_inside_constructor_ordering() -> None:
    env = Environment(debug=True)
    server = Server(env=env, capacity=1)
    job = _job(env, server)
    seen = _record(env)

    request = server.request(job=job)

    assert request.triggered
    assert [e.type_name for e in seen] == ["job.queued", "job.granted"]
    queued, granted = seen
    assert isinstance(queued, JobQueued)
    assert (queued.job, queued.server, queued.queue_length, queued.priority) == (job.id, server.id, 1, 0.0)
    assert queued.deltas.ops == (
        ("insert", server.id, "queue", 0, job.id),
        ("set", job.id, "location", f"queue:{server.id}"),
    )
    assert isinstance(granted, JobGranted)
    assert (granted.job, granted.server) == (job.id, server.id)
    assert granted.deltas.ops == (
        ("remove", server.id, "queue", job.id),
        ("insert", server.id, "users", 0, job.id),
        ("set", job.id, "location", f"server:{server.id}"),
    )


def test_direct_server_use_without_shopfloor() -> None:
    env = Environment(debug=True)
    server = Server(env=env, capacity=1, name="lathe")
    job = _job(env, server)
    seen = _record(env)
    replay = ReplayChecker(env)
    replay.state = env.entities.snapshot()

    env.process(_hold(env, server, job, 3.0))
    env.run()

    assert [(e.type_name, e.t) for e in seen] == [("job.queued", 0), ("job.granted", 0), ("job.released", 3)]
    released = seen[-1]
    assert isinstance(released, JobReleased)
    assert (released.job, released.server) == (job.id, "lathe")
    assert released.deltas.ops == (("remove", "lathe", "users", job.id), ("set", job.id, "location", "transit"))
    assert replay.state[job.id]["location"] == "transit"
    assert replay.checked == ["job.queued", "job.granted", "job.released"]
    assert job.servers_entry_at[server] == 0
    assert job.servers_exit_at[server] == 3
    assert job.current_server is server


def test_direct_process_job_replays_worked_time(tmp_path: Any) -> None:
    """Every change of replayed server state carries a delta at the server level (ruling R29, spec §17): a server
    used without a ShopFloor, through ``process_job``, replays its ``worked_time`` (Codex probe: live 5.0, replay
    0.0, with verify() and check() passing)."""
    from simulatte.trace import ChunkLimits, Trace, TraceRecorder

    env = Environment(seed=1, debug=True)
    server = Server(env=env, capacity=1, name="lathe")
    job = _job(env, server)
    path = tmp_path / "direct.simtrace"
    recorder = TraceRecorder(env, path, chunk_limits=ChunkLimits(max_events=1))
    replay = ReplayChecker(env)
    replay.state = env.entities.snapshot()

    def work() -> ProcessGenerator:
        with server.request(job=job) as request:
            yield request
            yield from server.process_job(job, 5.0)
            yield from server.process_job(job, 2.5)

    env.process(work())
    env.run()
    recorder.close()

    assert server.worked_time == 7.5
    assert replay.state["lathe"]["worked_time"] == 7.5
    trace = Trace.open(path)
    assert trace.state_at(trace.cursor_range[1])["lathe"]["worked_time"] == 7.5  # ty: ignore[not-subscriptable]
    credited = [event for event in trace.events() if event.type == "server.work_credited"]
    assert [(e.t, dict(e.payload), e.deltas) for e in credited] == [
        (5.0, {"server": "lathe", "job": job.id, "processing_time": 5.0}, (("set", "lathe", "worked_time", 5.0),)),
        (7.5, {"server": "lathe", "job": job.id, "processing_time": 2.5}, (("set", "lathe", "worked_time", 7.5),)),
    ]
    assert trace.verify() is True
    trace.check()


def test_replay_equals_live_at_every_event() -> None:
    """Capacity 2, dynamic priorities, simultaneous releases, an interrupt and a duplicate release."""
    env = Environment(debug=True)
    replay = ReplayChecker(env)  # subscribed before any entity exists: replay starts from {}
    seen = _record(env)
    server = Server(env=env, capacity=2)
    ranks: dict[str, float] = {}

    def rank(job: Any, _server: Server) -> float:
        return ranks.get(job.id, 0.0)

    jobs = [_job(env, server, priority_policy=rank) for _ in range(6)]
    for i, job in enumerate(jobs):
        ranks[job.id] = float(10 - i)  # later jobs rank better while waiting

    def interrupted(job: ProductionJob) -> ProcessGenerator:
        try:
            with server.request(job=job) as request:
                yield request
                yield env.timeout(1.0)
        except simpy.Interrupt:
            pass

    def double_release(job: ProductionJob) -> ProcessGenerator:
        request = server.request(job=job)
        yield request
        yield env.timeout(2.0)
        server.release(request)
        server.release(request)

    def driver() -> ProcessGenerator:
        env.process(_hold(env, server, jobs[0], 4.0))
        env.process(_hold(env, server, jobs[1], 4.0))
        victim = env.process(interrupted(jobs[2]))
        env.process(_hold(env, server, jobs[3], 1.0))
        env.process(double_release(jobs[4]))
        yield env.timeout(1.0)
        ranks[jobs[3].id] = -5.0  # moves to the front of the waiting queue
        server.sort_queue()
        victim.interrupt()
        env.process(_hold(env, server, jobs[5], 1.0))

    env.process(driver())
    env.run()

    types = [e.type_name for e in seen]
    assert types.count("job.queued") == 6
    assert types.count("job.granted") == 5
    assert types.count("job.released") == 5
    assert types.count("job.queue_left") == 1
    assert "server.queue_reordered" in types
    assert env.entities.snapshot()[server.id]["users"] == ()
    assert env.entities.snapshot()[server.id]["queue"] == ()
    assert replay.state == env.entities.snapshot()
    assert len(replay.checked) == len([e for e in seen if isinstance(e, DomainEvent)]) + 7  # + entity.created


def test_reorder_emits_minimal_moves() -> None:
    env = Environment(debug=True)
    server = Server(env=env, capacity=1)
    ranks: dict[str, float] = {}

    def rank(job: Any, _server: Server) -> float:
        return ranks[job.id]

    blocker = _job(env, server, priority_policy=rank)
    waiting = [_job(env, server, priority_policy=rank) for _ in range(4)]
    for i, job in enumerate([blocker, *waiting]):
        ranks[job.id] = float(i)
    env.process(_hold(env, server, blocker, 10.0))
    for job in waiting:
        env.process(_hold(env, server, job, 1.0))
    env.run(until=1)
    replay = ReplayChecker(env)
    replay.state = env.entities.snapshot()
    seen = _record(env, (ServerQueueReordered,))

    server.sort_queue()  # nothing changed
    assert seen == []

    ranks[waiting[3].id] = -1.0
    server.sort_queue()

    assert len(seen) == 1
    reordered = seen[0]
    assert isinstance(reordered, ServerQueueReordered)
    assert reordered.server == server.id
    assert reordered.deltas.ops == (("move", server.id, "queue", waiting[3].id, 0),)
    assert replay.state[server.id]["queue"] == (waiting[3].id, waiting[0].id, waiting[1].id, waiting[2].id)

    # Reverse the queue: n - 1 moves, the minimum for a reversal.
    for i, job in enumerate([waiting[2], waiting[1], waiting[0], waiting[3]]):
        ranks[job.id] = float(i)
    server.sort_queue()
    assert len(seen) == 2
    assert len(seen[1].deltas) == 3
    assert replay.state[server.id]["queue"] == (waiting[2].id, waiting[1].id, waiting[0].id, waiting[3].id)


def test_interrupted_waiting_request_leaves_queue() -> None:
    env = Environment(debug=True)
    server = Server(env=env, capacity=1)
    blocker, waiter = _job(env, server), _job(env, server)
    replay = ReplayChecker(env)
    replay.state = env.entities.snapshot()
    seen = _record(env)

    def wait() -> ProcessGenerator:
        try:
            with server.request(job=waiter) as request:
                yield request
        except simpy.Interrupt:
            pass

    env.process(_hold(env, server, blocker, 5.0))
    process = env.process(wait())

    def interrupter() -> ProcessGenerator:
        yield env.timeout(1.0)
        process.interrupt()

    env.process(interrupter())
    env.run()

    left = [e for e in seen if isinstance(e, JobQueueLeft)]
    assert len(left) == 1
    assert (left[0].job, left[0].server, left[0].reason, left[0].t) == (waiter.id, server.id, "cancelled", 1)
    assert left[0].deltas.ops == (("remove", server.id, "queue", waiter.id), ("set", waiter.id, "location", "transit"))
    # The with-block also releases the ungranted request: that changes nothing and emits nothing.
    assert [e.job for e in seen if isinstance(e, JobReleased)] == [blocker.id]
    assert replay.state == env.entities.snapshot()


def test_duplicate_release_emits_nothing() -> None:
    env = Environment(debug=True)
    server = Server(env=env, capacity=1)
    job = _job(env, server)
    request = server.request(job=job)
    seen = _record(env)

    server.release(request)
    server.release(request)
    env.run()

    assert [e.type_name for e in seen] == ["job.released"]


def _counting_policy_run(subscribe: bool) -> tuple[int, list[tuple[str, object, object]]]:
    env = Environment(seed=7)
    received: list[Event] = []
    if subscribe:
        env.bus.subscribe(received.append, "**")
    sf = ShopFloor(env=env)
    servers = [Server(env=env, capacity=1, shopfloor=sf) for _ in range(2)]
    calls = 0

    def policy(job: Any, server: Server) -> float:
        nonlocal calls
        calls += 1
        return float((calls * 7 + int(job.id.split("-")[1])) % 5)

    jobs: list[ProductionJob] = []

    def source() -> ProcessGenerator:
        for i in range(12):
            job = ProductionJob(
                env=env,
                sku="A",
                servers=[servers[i % 2], servers[(i + 1) % 2]],
                processing_times=[1.0 + i % 3, 2.0],
                due_date=50.0,
                priority_policy=policy,
            )
            jobs.append(job)
            sf.add(job)
            yield env.timeout(0.5)

    env.process(source())
    env.run()
    assert bool(received) == subscribe
    schedule: list[tuple[str, object, object]] = [
        (job.id, job.servers_entry_at[s], job.servers_exit_at[s]) for job in jobs for s in job.servers
    ]
    return calls, schedule


def test_counting_priority_policy_unaffected_by_recording() -> None:
    plain_calls, plain_schedule = _counting_policy_run(subscribe=False)
    recorded_calls, recorded_schedule = _counting_policy_run(subscribe=True)

    assert plain_calls > 0
    assert recorded_calls == plain_calls
    assert recorded_schedule == plain_schedule


def _priority_run(policy: Any, observe: str, tmp_path: Any) -> tuple[list[tuple[str, float]], list[Any]]:
    """Jobs with `policy` contend for one server; returns (job id, finish time) and the recorded priorities."""
    from simulatte.trace import Trace, TraceRecorder

    env = Environment(seed=3)
    path = tmp_path / f"{observe}.simtrace"
    queued: list[JobQueued] = []
    if observe == "subscriber":
        env.bus.subscribe(lambda event: queued.append(event) if isinstance(event, JobQueued) else None, "*")
    elif observe == "recorder":
        TraceRecorder(env, path)
    server = Server(env=env, capacity=1, name="s")
    done: list[tuple[str, float]] = []

    def run_job(i: int) -> ProcessGenerator:
        sku = f"k{i % 3}"
        job = ProductionJob(
            env=env, sku=sku, servers=[server], processing_times=[1.0], due_date=10.0 - i, priority_policy=policy
        )
        yield env.timeout(0.1 * i)
        yield env.process(_hold(env, server, job, 1.0))
        done.append((job.id, env.now))

    for i in range(6):
        env.process(run_job(i))
    env.run()
    env.close()
    priorities: list[Any] = [event.priority for event in queued]
    if observe == "recorder":
        priorities = [event.payload["priority"] for event in Trace.open(path).events() if event.type == "job.queued"]
        assert Trace.open(path).verify() is True
    return done, priorities


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (lambda job, server: (job.due_date, job.sku), lambda job_due, sku: (job_due, sku)),
        (lambda job, server: job.sku, lambda job_due, sku: sku),
        (lambda job, server: _Opaque(job.due_date), lambda job_due, sku: None),
    ],
    ids=["tuple", "str", "opaque"],
)
def test_any_priority_simpy_accepts_is_recorded_without_raising(policy: Any, expected: Any, tmp_path: Any) -> None:
    """Observer invariance (spec §6.1): building job.queued never raises for a priority SimPy can sort, and never
    calls user code (no repr); priorities that are not wire values are recorded as None."""
    plain, _ = _priority_run(policy, "none", tmp_path)
    observed, live = _priority_run(policy, "subscriber", tmp_path)
    recorded, stored = _priority_run(policy, "recorder", tmp_path)

    assert observed == plain and recorded == plain
    dues = {f"job-{i}": (10.0 - i, f"k{i % 3}") for i in range(6)}
    assert live == stored == [expected(*dues[f"job-{i}"]) for i in range(6)]


class _Opaque:
    """A sortable priority that is not a wire value; its repr would be user code (and hold an address)."""

    def __init__(self, key: float) -> None:
        self.key = key

    def __lt__(self, other: _Opaque) -> bool:
        return self.key < other.key

    def __repr__(self) -> str:  # pragma: no cover - must never be called
        raise AssertionError("event construction called repr() on the priority")


def test_numeric_priorities_stay_float() -> None:
    env = Environment(seed=1)
    seen = _record(env, (JobQueued,))
    server = Server(env=env, capacity=1, name="s")
    for value in (3, 2.5, True, 10**400):
        policy = (lambda v: lambda job, server: v)(value)
        env.process(_hold(env, server, _job(env, server, priority_policy=policy), 1.0))
    env.run()
    priorities = [event.priority for event in seen if isinstance(event, JobQueued)]
    assert priorities == [3.0, 2.5, 1.0, None]
    assert [type(p) for p in priorities[:3]] == [float, float, float]


def _float_subclass_priority_run(observe: bool) -> tuple[int, float, list[Any]]:
    """Codex probe: a priority whose own ``__float__`` draws from the model's RNG stream."""
    env = Environment(seed=42)
    rng = env.rng("model")
    calls: list[float] = []

    class Priority(float):
        def __float__(self) -> float:
            calls.append(rng.random())
            return float.__float__(self)

    class Rank(int):
        def __float__(self) -> float:
            calls.append(rng.random())
            return 0.0

    seen: list[Event] = []
    if observe:
        env.bus.subscribe(seen.append, (JobQueued,))
    policies = [
        lambda job, server: Priority(0.5),
        lambda job, server: Rank(2),
        lambda job, server: (Priority(1.5), "x"),
    ]

    def process(policy: Any) -> ProcessGenerator:
        server = Server(env=env, capacity=1)  # one per job: the three priorities are not mutually comparable
        job = _job(env, server, priority_policy=policy)
        with server.request(job=job) as request:
            yield request
            yield env.timeout(rng.random())

    for policy in policies:
        env.process(process(policy))
    env.run()
    env.close()
    return len(calls), env.now, [event.priority for event in seen if isinstance(event, JobQueued)]


def test_numeric_subclass_priorities_are_read_without_user_code() -> None:
    """A no-op observer changes nothing: job.queued reads int and float subclasses through the built-in
    conversions, never through their own ``__float__`` (spec §6.1, §13)."""
    plain_calls, plain_end, _ = _float_subclass_priority_run(observe=False)
    observed_calls, observed_end, priorities = _float_subclass_priority_run(observe=True)

    assert plain_calls == observed_calls == 0
    assert observed_end == plain_end
    assert priorities == [0.5, 2.0, (1.5, "x")]
    assert [type(p) for p in priorities[:2]] == [float, float] and type(priorities[2][0]) is float


def test_location_strings_are_built_once_per_server() -> None:
    env = Environment(seed=1)
    server = Server(env=env, capacity=1, name="s")
    first, second = _job(env, server), _job(env, server)
    env.process(_hold(env, server, first, 1.0))
    env.process(_hold(env, server, second, 1.0))
    env.run(until=0.5)
    assert (first._location, second._location) == ("server:s", "queue:s")
    assert first._location is server._server_location and second._location is server._queue_location
