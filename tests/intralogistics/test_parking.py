from __future__ import annotations

import pytest
import simpy
from simpy.events import ProcessGenerator

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.events import ParkingEntered, ParkingLeft
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.speed import TrapezoidalProfile

from tests.intralogistics.test_resource_events import FullReplay


@pytest.fixture
def env() -> Environment:
    return Environment()


@pytest.fixture
def parking_node() -> Node:
    return Node(id="PARK", x=0.0, y=0.0)


@pytest.fixture
def agv_type() -> AGVType:
    return AGVType(
        name="test-agv",
        speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
        battery_capacity=100.0,
        weight_capacity=100.0,
        volume_capacity=1.0,
    )


def _make_agv(env: Environment, agv_type: AGVType, agv_id: str | None = None) -> AGV:
    return AGV(env=env, agv_type=agv_type, agv_id=agv_id)


class TestEnterLeaveLifecycle:
    def test_enter_occupies_slot_leave_frees_it(self, env: Environment, parking_node: Node, agv_type: AGVType) -> None:
        """AGV enters parking, slot is occupied. After leave, a new AGV can enter immediately."""
        area = ParkingArea(env=env, name="P1", node=parking_node, capacity=1)
        agv1 = _make_agv(env, agv_type, agv_id="AGV-1")
        agv2 = _make_agv(env, agv_type, agv_id="AGV-2")

        enter_times: dict[str, float] = {}

        def park_and_wait(agv: AGV) -> ProcessGenerator:
            yield from area.enter(agv)
            enter_times[agv.agv_id] = env.now
            # Stay parked for 10 time units, then leave
            yield env.timeout(10)
            area.leave(agv)

        def park_after(agv: AGV) -> ProcessGenerator:
            # Start entering at time 0 -- will block until slot is free
            yield from area.enter(agv)
            enter_times[agv.agv_id] = env.now

        env.process(park_and_wait(agv1))
        env.process(park_after(agv2))
        env.run()

        # AGV1 enters at t=0, leaves at t=10. AGV2 enters at t=10.
        assert enter_times["AGV-1"] == pytest.approx(0.0)
        assert enter_times["AGV-2"] == pytest.approx(10.0)


class TestBlockingWhenFull:
    def test_second_agv_blocks_until_first_leaves(
        self, env: Environment, parking_node: Node, agv_type: AGVType
    ) -> None:
        """Capacity=1, two AGVs. Second blocks until first leaves."""
        area = ParkingArea(env=env, name="P1", node=parking_node, capacity=1)
        agv1 = _make_agv(env, agv_type, agv_id="AGV-1")
        agv2 = _make_agv(env, agv_type, agv_id="AGV-2")

        enter_times: list[float] = []

        def park_briefly(agv: AGV, stay: float) -> ProcessGenerator:
            yield from area.enter(agv)
            enter_times.append(env.now)
            yield env.timeout(stay)
            area.leave(agv)

        env.process(park_briefly(agv1, stay=5.0))
        env.process(park_briefly(agv2, stay=3.0))
        env.run()

        # AGV1 enters at t=0, leaves at t=5. AGV2 enters at t=5, leaves at t=8.
        assert enter_times == [pytest.approx(0.0), pytest.approx(5.0)]
        assert env.now == pytest.approx(8.0)


class TestPerAGVRequestTracking:
    def test_correct_agv_is_released(self, env: Environment, parking_node: Node, agv_type: AGVType) -> None:
        """Capacity=2, two AGVs parked. Leave one; a third AGV enters immediately."""
        area = ParkingArea(env=env, name="P2", node=parking_node, capacity=2)
        agv_a = _make_agv(env, agv_type, agv_id="agv-A")
        agv_b = _make_agv(env, agv_type, agv_id="agv-B")
        agv_c = _make_agv(env, agv_type, agv_id="agv-C")

        enter_time_c: float = -1.0

        def park_both_then_release_a() -> ProcessGenerator:
            nonlocal enter_time_c
            # Both enter at t=0
            yield from area.enter(agv_a)
            yield from area.enter(agv_b)
            # At t=5 release A (not B)
            yield env.timeout(5)
            area.leave(agv_a)

        def try_enter_c() -> ProcessGenerator:
            nonlocal enter_time_c
            # C tries to enter at t=0, but area is full at t=0 (both A and B enter first)
            # Wait a tiny bit so A and B definitely get in first
            yield env.timeout(1)
            yield from area.enter(agv_c)
            enter_time_c = env.now

        env.process(park_both_then_release_a())
        env.process(try_enter_c())
        env.run()

        # C should enter at t=5 when A leaves (not before)
        assert enter_time_c == pytest.approx(5.0)

        # B should still be tracked
        assert agv_b in area._agv_requests
        # A should no longer be tracked
        assert agv_a not in area._agv_requests


class TestAvailableCapacity:
    def test_full_capacity_when_empty(self, env: Environment, parking_node: Node) -> None:
        """Available capacity equals total capacity when no AGVs are parked."""
        area = ParkingArea(env=env, name="P1", node=parking_node, capacity=3)
        assert area.available_capacity == 3

    def test_decremented_after_enter(self, env: Environment, parking_node: Node, agv_type: AGVType) -> None:
        """Available capacity decreases by one after an AGV enters."""
        area = ParkingArea(env=env, name="P1", node=parking_node, capacity=2)
        agv = _make_agv(env, agv_type, agv_id="AGV-1")

        def park(agv: AGV) -> ProcessGenerator:
            yield from area.enter(agv)

        env.process(park(agv))
        env.run()

        assert area.available_capacity == 1

    def test_restored_after_leave(self, env: Environment, parking_node: Node, agv_type: AGVType) -> None:
        """Available capacity is restored after an AGV leaves."""
        area = ParkingArea(env=env, name="P1", node=parking_node, capacity=1)
        agv = _make_agv(env, agv_type, agv_id="AGV-1")

        def park_and_leave(agv: AGV) -> ProcessGenerator:
            yield from area.enter(agv)
            area.leave(agv)

        env.process(park_and_leave(agv))
        env.run()

        assert area.available_capacity == 1


class TestRepr:
    def test_repr_contains_name(self, parking_node: Node) -> None:
        """Repr includes the parking area name."""
        env = Environment()
        area = ParkingArea(env=env, name="Main-Parking", node=parking_node, capacity=5)
        r = repr(area)
        assert "Main-Parking" in r
        assert "ParkingArea" in r


@pytest.mark.parametrize("capacity", [1, 2])
def test_enter_already_parked_is_noop(env: Environment, parking_node: Node, agv_type: AGVType, capacity: int) -> None:
    area = ParkingArea(env=env, name="P", node=parking_node, capacity=capacity)
    agv = _make_agv(env, agv_type)

    def park_twice() -> ProcessGenerator:
        yield from area.enter(agv)
        original = area._agv_requests[agv]
        yield from area.enter(agv)
        assert area._agv_requests[agv] is original
        assert area.available_capacity == capacity - 1
        area.leave(agv)

    process = env.process(park_twice())
    env.run()
    assert process.triggered
    assert area.available_capacity == capacity
    assert not area._resource.queue


@pytest.mark.parametrize("capacity", [1, 2])
@pytest.mark.parametrize("leave_immediately", [False, True])
def test_simultaneous_entries_share_one_admission(
    env: Environment, parking_node: Node, agv_type: AGVType, capacity: int, leave_immediately: bool
) -> None:
    replay = FullReplay(env)
    area = ParkingArea(env=env, name="P", node=parking_node, capacity=capacity)
    agv = _make_agv(env, agv_type)

    def first_entry() -> ProcessGenerator:
        yield from area.enter(agv)
        if leave_immediately:
            area.leave(agv)

    first = env.process(first_entry())
    second = env.process(area.enter(agv))
    env.run()
    assert first.triggered and second.triggered
    assert area.available_capacity == (capacity if leave_immediately else capacity - 1)
    assert len(replay.of(ParkingEntered)) == 1
    if not leave_immediately:
        area.leave(agv)
    env.run()
    assert area.available_capacity == capacity
    assert len(replay.of(ParkingLeft)) == 1
    assert not area._resource.queue
    assert not area._agv_requests


@pytest.mark.parametrize("phase", ["queued", "immediate_grant", "delayed_grant"])
def test_interrupted_entry_releases_its_reservation(
    env: Environment, parking_node: Node, agv_type: AGVType, phase: str
) -> None:
    replay = FullReplay(env)
    area = ParkingArea(env=env, name="P", node=parking_node, capacity=1)
    agv = _make_agv(env, agv_type)
    blocker = _make_agv(env, agv_type)
    interrupted: list[object] = []

    def enter() -> ProcessGenerator:
        try:
            yield from area.enter(agv)
        except simpy.Interrupt as exc:
            interrupted.append(exc.cause)

    if phase != "immediate_grant":
        env.process(area.enter(blocker))
        env.run()
    waiting = env.process(enter())
    env.activate()
    env.step()  # Start the request, before a granted request resumes its caller.
    if phase == "delayed_grant":
        request = area._resource.queue[0]
        area.leave(blocker)
        while not request.triggered:
            env.step()
    waiting.interrupt("cancel parking")
    env.run()
    if phase == "queued":
        area.leave(blocker)
        env.run()
    assert interrupted == ["cancel parking"]
    assert area.available_capacity == 1
    assert not area._resource.queue
    assert not area._agv_requests
    assert not [event for event in replay.of(ParkingEntered, ParkingLeft) if event.agv == agv.id]
    # Cancellation must allow a fresh admission for the same AGV.
    retry = env.process(area.enter(agv))
    env.run()
    assert retry.triggered
    assert area.available_capacity == 0
    assert len([event for event in replay.of(ParkingEntered) if event.agv == agv.id]) == 1


@pytest.mark.parametrize("interrupted_index", [0, 1])
@pytest.mark.parametrize("cancel_both", [False, True])
def test_duplicate_waiters_cancel_independently(
    env: Environment, parking_node: Node, agv_type: AGVType, interrupted_index: int, cancel_both: bool
) -> None:
    replay = FullReplay(env)
    area = ParkingArea(env=env, name="P", node=parking_node, capacity=1)
    agv = _make_agv(env, agv_type)
    blocker = _make_agv(env, agv_type)
    env.process(area.enter(blocker))
    env.run()

    def enter() -> ProcessGenerator:
        try:
            yield from area.enter(agv)
        except simpy.Interrupt:
            pass

    waiters = [env.process(enter()), env.process(enter())]
    env.run()
    waiters[interrupted_index].interrupt()
    env.run()
    assert len(area._resource.queue) == 1
    assert not waiters[1 - interrupted_index].triggered
    if cancel_both:
        waiters[1 - interrupted_index].interrupt()
        env.run()
    area.leave(blocker)
    env.run()
    assert all(waiter.triggered for waiter in waiters)
    assert area.available_capacity == int(cancel_both)
    assert not area._resource.queue
    assert len([event for event in replay.of(ParkingEntered) if event.agv == agv.id]) == int(not cancel_both)
