"""``Environment.unschedule``: a scheduled event taken out of the event queue never fires and never moves the clock."""

from __future__ import annotations

import math

import pytest
import simpy
from simpy.core import URGENT

from simulatte.environment import Environment


def timeout(env: Environment, fired: list[tuple[str, float]], name: str, delay: float) -> simpy.Timeout:
    """A timeout whose callback records its name and the time it fired."""
    event = env.timeout(delay)
    event.callbacks.append(lambda _: fired.append((name, float(env.now))))
    return event


def test_other_events_keep_their_order_and_the_removed_one_never_fires() -> None:
    env = Environment()
    fired: list[tuple[str, float]] = []
    timeout(env, fired, "before", 5.0)
    timeout(env, fired, "same-earlier", 10.0)
    removed = timeout(env, fired, "removed", 10.0)
    timeout(env, fired, "same-later", 10.0)
    urgent = env.event()
    urgent.callbacks.append(lambda _: fired.append(("same-urgent", float(env.now))))
    urgent._ok, urgent._value = True, None
    env.schedule(urgent, URGENT, 10.0)
    timeout(env, fired, "after", 15.0)
    env.unschedule(removed)
    env.run()
    assert fired == [
        ("before", 5.0),
        ("same-urgent", 10.0),
        ("same-earlier", 10.0),
        ("same-later", 10.0),
        ("after", 15.0),
    ]
    assert env.now == 15.0


def test_run_ends_at_the_last_remaining_event() -> None:
    env = Environment()
    fired: list[tuple[str, float]] = []
    timeout(env, fired, "first", 5.0)
    timeout(env, fired, "second", 10.0)
    removed = timeout(env, fired, "removed", 20.0)
    env.unschedule(removed)
    assert env.peek() == 5.0
    env.run()
    assert env.now == 10.0
    assert fired == [("first", 5.0), ("second", 10.0)]


def test_removing_the_only_event_empties_the_queue() -> None:
    env = Environment()
    fired: list[tuple[str, float]] = []
    removed = timeout(env, fired, "removed", 20.0)
    env.unschedule(removed)
    assert env.peek() == math.inf
    env.run()
    assert env.now == 0.0
    assert fired == []


@pytest.mark.parametrize("at", [10.0, 25.0, 40.0], ids=["before-until", "at-until", "after-until"])
def test_run_until_is_the_same_with_and_without_the_removed_event(at: float) -> None:
    def scenario(with_removed: bool) -> tuple[list[tuple[str, float]], float, float]:
        env = Environment()
        fired: list[tuple[str, float]] = []
        timeout(env, fired, "a", 10.0)
        if with_removed:
            removed = timeout(env, fired, "removed", at)
        timeout(env, fired, "b", 25.0)
        timeout(env, fired, "c", 30.0)
        if with_removed:
            env.unschedule(removed)
        env.run(until=25.0)
        return fired, float(env.now), env.peek()

    assert scenario(True) == scenario(False) == ([("a", 10.0)], 25.0, 25.0)


def test_a_process_waiting_on_another_event_is_unaffected() -> None:
    env = Environment()
    fired: list[tuple[str, float]] = []
    removed = timeout(env, fired, "removed", 4.0)

    def proc():
        yield env.timeout(2.0)
        fired.append(("proc", float(env.now)))
        yield env.timeout(6.0)
        fired.append(("proc", float(env.now)))

    env.process(proc())
    env.unschedule(removed)
    env.run()
    assert fired == [("proc", 2.0), ("proc", 8.0)]


def test_an_event_that_is_not_scheduled_raises() -> None:
    env = Environment()
    fired: list[tuple[str, float]] = []
    with pytest.raises(ValueError, match="not in the event queue"):
        env.unschedule(env.event())  # never scheduled
    processed = timeout(env, fired, "processed", 1.0)
    env.run()
    with pytest.raises(ValueError, match="not in the event queue"):
        env.unschedule(processed)  # already processed
    removed = timeout(env, fired, "removed", 1.0)
    env.unschedule(removed)
    with pytest.raises(ValueError, match="not in the event queue"):
        env.unschedule(removed)  # already unscheduled
    assert fired == [("processed", 1.0)]
