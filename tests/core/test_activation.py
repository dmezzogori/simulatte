"""Preparation and activation: initializers, initial state, projection and the deferred command queue."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, ClassVar

import pytest
import simpy

from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment, deferrable
from simulatte.events import DomainEvent, Event, event_type


@event_type("test.activation_tick")
class Tick(DomainEvent):
    n: int = 0


class Gauge(Entity, kind="test_gauge"):
    state_schema: ClassVar[StateSchema] = StateSchema({"level": FieldSpec("float")})

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.level = 0.0
        env.entities.attach(self)

    @deferrable
    def set_level(self, level: float) -> None:
        self.level = level


class Console:
    """A component with deferrable commands."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.log: list[tuple[str, float, Any]] = []

    @deferrable
    def submit(self, item: str, *, priority: int = 0) -> str:
        self.log.append(("submit", self.env.now, (item, priority)))
        return f"ok:{item}"

    @deferrable
    def cancel(self, item: str) -> None:
        if item == "bad":
            raise ValueError("cannot cancel bad")
        self.log.append(("cancel", self.env.now, item))


def _record(env: Environment) -> list[Event]:
    seen: list[Event] = []
    env.bus.subscribe(seen.append, "**")
    return seen


# --- activation ------------------------------------------------------------------------------------------


def test_run_activates_once(env: Environment) -> None:
    calls: list[float] = []
    env.on_activate(lambda: calls.append(env.now))
    assert not env.activated

    env.run(until=5)
    assert env.activated and calls == [0]

    env.run(until=10)
    env.activate()
    assert calls == [0] and env.now == 10


def test_activate_is_idempotent_and_initializers_run_in_order(env: Environment) -> None:
    calls: list[str] = []
    env.on_activate(lambda: calls.append("a"))
    env.on_activate(lambda: (calls.append("b"), env.on_activate(lambda: calls.append("nested"))))
    env.activate()
    env.activate()
    assert calls == ["a", "b", "nested"]

    env.on_activate(lambda: calls.append("late"))
    assert calls == ["a", "b", "nested", "late"]


def test_initializer_cannot_create_process(env: Environment) -> None:
    def proc() -> Generator[Any, Any, None]:
        yield env.timeout(1)

    queued = len(env._queue)
    env.on_activate(lambda: env.process(proc()))
    with pytest.raises(RuntimeError, match="initializer"):
        env.run()
    assert len(env._queue) == queued and env.now == 0


def test_initializer_cannot_schedule_timeout(env: Environment) -> None:
    env.on_activate(lambda: env.timeout(3))
    with pytest.raises(RuntimeError, match="initializer"):
        env.activate()
    assert len(env._queue) == 0 and not env.activated


def test_initializer_cannot_succeed_event_with_callback(env: Environment) -> None:
    fired: list[object] = []
    event = env.event()
    event.callbacks.append(fired.append)
    env.on_activate(lambda: event.succeed("x"))
    with pytest.raises(RuntimeError, match="initializer"):
        env.activate()
    assert len(env._queue) == 0 and fired == []


def test_scheduling_is_restored_after_initializers(env: Environment) -> None:
    env.on_activate(lambda: None)
    env.activate()
    env.timeout(2)
    env.run()
    assert env.now == 2


def test_initializer_cannot_advance_time(env: Environment) -> None:
    env.timeout(3)  # scheduled during preparation, allowed
    env.on_activate(env.step)
    with pytest.raises(RuntimeError, match="advanced simulated time"):
        env.activate()


def test_failed_activation_cannot_be_retried(env: Environment) -> None:
    def boom() -> None:
        raise KeyError("boom")

    env.on_activate(boom)
    with pytest.raises(KeyError):
        env.run()
    with pytest.raises(RuntimeError, match="in progress or failed"):
        env.run()
    assert not env.activated


def test_activate_from_initializer_raises(env: Environment) -> None:
    env.on_activate(env.activate)
    with pytest.raises(RuntimeError, match="in progress"):
        env.activate()


def test_internal_scheduling_context_allowed(env: Environment) -> None:
    resource = simpy.Resource(env, capacity=1)
    requests: list[simpy.Event] = []

    def place() -> None:
        with env._internal_scheduling():
            requests.append(resource.request())
        with pytest.raises(RuntimeError, match="initializer"):
            env.timeout(1)  # the context is narrow: scheduling is blocked again after it

    env.on_activate(place)
    env.activate()
    (request,) = requests
    assert request.triggered and resource.count == 1 and env.now == 0

    env.run()
    assert request.processed and env.now == 0


def test_initializer_may_trigger_immediate_resource_grant(env: Environment) -> None:
    resource = simpy.Resource(env, capacity=1)
    gauge = Gauge(env)

    def place() -> None:
        with env._internal_scheduling():
            request = resource.request()
        assert request.triggered  # granted immediately
        gauge.level = 1.0

    env.on_activate(place)
    env.activate()
    assert len(resource.users) == 1 and env.initial_state[gauge.id]["level"] == 1.0

    other = resource.request()
    env.run(until=1)
    assert not other.triggered  # the slot stays held by the placement


def test_internal_scheduling_outside_initializers_is_noop(env: Environment) -> None:
    with env._internal_scheduling():
        env.timeout(1)
    env.timeout(2)
    env.run()
    assert env.now == 2


# --- initial state and projection ------------------------------------------------------------------------


def test_prelude_events_have_no_ordinal_and_first_is_zero(env: Environment) -> None:
    seen = _record(env)
    states: list[tuple[dict[str, Any], bool]] = []
    env.request_projection(lambda state: states.append((state, env._projection_active)))
    env.emit(Tick(n=1))  # prelude
    env.on_activate(lambda: env.emit(Tick(n=2)))  # initializers are still preparation

    def proc() -> Generator[Any, Any, None]:
        env.emit(Tick(n=3))
        yield env.timeout(1)
        env.emit(Tick(n=4))

    env.process(proc())
    env.run()
    assert [(e.n, e.ordinal) for e in seen if isinstance(e, Tick)] == [(1, None), (2, None), (3, 0), (4, 1)]
    assert states == [(env.initial_state, False)]


def test_projection_inactive_without_request(env: Environment) -> None:
    seen = _record(env)
    env.activate()
    env.emit(Tick())
    assert isinstance(seen[0], Tick) and seen[0].ordinal is None


def test_request_projection_after_activation_raises(env: Environment) -> None:
    env.activate()
    with pytest.raises(RuntimeError, match="before activation"):
        env.request_projection(lambda state: None)


def test_initial_state_captured_after_initializers(env: Environment) -> None:
    gauge = Gauge(env)
    with pytest.raises(RuntimeError, match="not activated"):
        _ = env.initial_state

    def initialize() -> None:
        gauge.level = 2.5

    env.on_activate(initialize)
    gauge.set_level(9.0)  # a queued command runs after the capture
    assert gauge.level == 0.0
    env.activate()
    assert env.initial_state == {gauge.id: {"$kind": "test_gauge", "level": 2.5, "label": gauge.label}}
    assert gauge.level == 9.0


def test_on_activate_from_projection_listener_runs_immediately(env: Environment) -> None:
    ran: list[str] = []

    def late() -> None:
        ran.append("late")
        env.timeout(1)  # still an initializer: scheduling is blocked

    def listener(state: Any) -> None:
        env.on_activate(lambda: ran.append("from listener"))

    env.request_projection(listener)
    env.activate()
    assert ran == ["from listener"]
    with pytest.raises(RuntimeError, match="cannot schedule"):
        env.on_activate(late)


def test_on_activate_from_listener_is_blocked_from_scheduling(env: Environment) -> None:
    def listener(state: Any) -> None:
        env.on_activate(lambda: env.timeout(1))

    env.request_projection(listener)
    with pytest.raises(RuntimeError, match="cannot schedule"):
        env.activate()


# --- command queue ---------------------------------------------------------------------------------------


def test_deferred_commands_preserve_order(env: Environment) -> None:
    console = Console(env)
    assert console.submit("a", priority=2) is None
    assert console.cancel("a") is None
    assert console.submit("b") is None
    assert console.log == []

    env.run()
    assert console.log == [("submit", 0, ("a", 2)), ("cancel", 0, "a"), ("submit", 0, ("b", 0))]


def test_deferred_commands_drain_before_time_zero_events(env: Environment) -> None:
    console = Console(env)
    seen: list[str] = []
    event = env.event()
    event.callbacks.append(lambda _: seen.append(f"event after {len(console.log)} commands"))
    event.succeed()
    console.submit("a")
    console.submit("b")
    env.run()
    assert seen == ["event after 2 commands"]


def test_deferred_command_failure_stops_activation(env: Environment) -> None:
    console = Console(env)
    console.submit("a")
    console.cancel("bad")
    console.submit("c")
    with pytest.raises(ValueError, match="cannot cancel bad"):
        env.run()
    assert console.log == [("submit", 0, ("a", 0))]

    env.run()  # remaining commands were dropped
    assert console.log == [("submit", 0, ("a", 0))]


def test_after_activation_commands_run_immediately(env: Environment) -> None:
    console = Console(env)
    env.activate()
    assert console.submit("a") == "ok:a"

    def later() -> Generator[Any, Any, None]:
        yield env.timeout(4)
        console.cancel("a")

    env.process(later())
    env.run()
    assert console.log == [("submit", 0, ("a", 0)), ("cancel", 4, "a")]


def test_command_queued_while_draining_runs_immediately(env: Environment) -> None:
    console = Console(env)

    class Chain:
        def __init__(self) -> None:
            self.env = env

        @deferrable
        def go(self) -> None:
            console.submit("inner")
            console.log.append(("after-inner", env.now, None))

    Chain().go()
    console.submit("outer")
    env.activate()
    assert [entry[0] for entry in console.log] == ["submit", "after-inner", "submit"]
    assert [entry[2] for entry in console.log if entry[0] == "submit"] == [("inner", 0), ("outer", 0)]
