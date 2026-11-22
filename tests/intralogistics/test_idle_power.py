"""Idle power in travels, missions and charging (spec §9.5).

``AGVType.idle_power`` is energy per unit time in every state except ``CHARGING``. It accrues lazily: the level is
brought up to date at each event of the AGV that reads or changes the battery, with ``agv.battery_changed`` when an
accrual outside a move changes it; the drain of a move is folded into ``agv.move_ended``. The plan expects the level
at each node with the idle drain of the planned time included, so that nothing unforeseen happens at a node.

Every world runs :class:`FullReplay`, which compares the replayed battery of every AGV with the live one after each
event; worlds with a ``ReservationController`` also run :class:`TrafficInvariants`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from simpy.events import ProcessGenerator

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.battery import Battery
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.events import (
    AgvBatteryChanged,
    AgvMoveEnded,
    AgvMoveStarted,
    ChargingStarted,
    TrafficWaitEnded,
    TrafficWaitStarted,
)
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.order import OrderStatus
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.travel import TravelOutcome, _Trip
from simulatte.intralogistics.warehouse import Warehouse
from tests.intralogistics.test_stop_requests import weak
from tests.intralogistics.test_travel import Plain, World, vehicle, world
from tests.intralogistics.test_turning import run_l, turning
from tests.intralogistics.traffic_support import FullReplay, StillFleet, build, line


def powered(power: float, **options: Any) -> AGVType:
    """The standard test vehicle (battery 1000, depletion 1 per metre) with `power` of idle power."""
    return replace(vehicle(), idle_power=power, **options)


def side_branch() -> Any:
    """``N0 - N1 - N2`` with ``S`` hanging off ``N1``: a vehicle on ``N1`` blocks the line until it moves to ``S``."""
    return build([("N0", 0, 0), ("N1", 10, 0), ("N2", 20, 0), ("S", 10, 10)], [("N0", "N1"), ("N1", "N2"), ("N1", "S")])


def changes(w: World, agv: str) -> list[tuple[float, float]]:
    return [(e.t, e.battery) for e in w.of(AgvBatteryChanged, agv=agv)]


# --- waits, moves ------------------------------------------------------------------------------------------------


def test_a_traffic_wait_of_20_costs_10_and_is_accounted_at_the_next_energy_check() -> None:
    w = world(side_branch(), {"F": "N0", "L": "N1"}, reserve=True, agv_type=powered(0.5))
    w.go("F", "N2")
    w.go("L", "S", at=20.0)
    w.env.run()
    assert w.outcomes == {"F": TravelOutcome.ARRIVED, "L": TravelOutcome.ARRIVED}
    (started,) = w.of(TrafficWaitStarted, agv="F")
    (ended,) = w.of(TrafficWaitEnded, agv="F")
    assert (started.t, ended.t) == (0.0, 20.0)
    assert changes(w, "F") == [(20.0, 990.0)]  # nothing is emitted while it waits: the check at 20 finds the level
    assert changes(w, "L") == [(20.0, 990.0)]  # the blocker idled for 20 before its own travel
    assert w.events.index(w.of(AgvBatteryChanged, agv="F")[0]) < w.events.index(w.of(AgvMoveStarted, agv="F")[0])


def test_the_drain_of_a_move_is_folded_into_move_ended_and_nothing_is_emitted_inside_it() -> None:
    w = world(side_branch(), {"F": "N0", "L": "N1"}, reserve=True, agv_type=powered(0.5))
    w.go("F", "N2")
    w.go("L", "S", at=20.0)
    w.env.run()
    started, ended = w.of(AgvMoveStarted, agv="F"), w.of(AgvMoveEnded, agv="F")
    # Each arc: its depletion of 10, then 0.5 per second of its duration (FullReplay replays the level at every event).
    level = 990.0
    for begin, event in zip(started, ended):
        level -= 10.0
        level -= 0.5 * (event.t - begin.t)
        assert event.battery == pytest.approx(level, rel=1e-12)
    assert ended[-1].battery == pytest.approx(990.0 - 20.0 - 0.5 * (ended[-1].t - 20.0), rel=1e-12)
    first_start = w.events.index(started[0])
    last_end = w.events.index(ended[-1])
    assert not [e for e in w.events[first_start:last_end] if isinstance(e, AgvBatteryChanged) and e.agv == "F"]


@pytest.mark.parametrize("reserve", [False, True])
def test_a_turn_wait_is_accounted_when_it_ends(reserve: bool) -> None:
    w = run_l(turning(turn_time=lambda angle: 4.0, idle_power=0.5), reserve=reserve)
    first, second = w.of(AgvMoveStarted)
    ended = w.of(AgvMoveEnded)
    ((t, battery),) = changes(w, "A")
    assert t == second.t == ended[0].t + 4.0
    assert battery == ended[0].battery - 2.0
    assert ended[1].battery < battery - 10.0  # the second arc drains again
    assert first.t == 0.0


def test_a_profile_without_limits_accrues_over_a_wait_and_in_a_move() -> None:
    w = world(side_branch(), {"F": "N0", "L": "N1"}, reserve=True, agv_type=powered(0.5, speed_profile=Plain()))
    w.go("F", "N2")
    w.go("L", "S", at=20.0)
    w.env.run()
    assert changes(w, "F") == [(20.0, 990.0)]
    assert [e.battery for e in w.of(AgvMoveEnded, agv="F")] == [990.0 - 10.0 - 2.5, 990.0 - 20.0 - 5.0]


# --- the default changes nothing ---------------------------------------------------------------------------------


def test_without_idle_power_nothing_is_emitted_and_no_level_is_computed(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(self: Battery, amount: float) -> None:
        raise AssertionError("the battery was drained")

    monkeypatch.setattr(Battery, "drain", forbidden)
    w = world(side_branch(), {"F": "N0", "L": "N1"}, reserve=True, agv_type=vehicle())
    w.go("F", "N2")
    w.go("L", "S", at=20.0)
    w.env.run()
    assert w.outcomes == {"F": TravelOutcome.ARRIVED, "L": TravelOutcome.ARRIVED}
    assert not w.of(AgvBatteryChanged)
    assert [e.battery for e in w.of(AgvMoveEnded, agv="F")] == [990.0, 980.0]
    assert w.agvs["L"].battery.level == 990.0


# --- the plan expects the idle drain -----------------------------------------------------------------------------


def test_with_idle_power_the_levels_at_node_events_are_the_expected_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan subtracts the idle drain of each planned arc from the level it expects at the node, by the operations
    of the fold, so a long route under free flow scans the affordable end no more often than a short one and never asks
    for an energy stop (1 m arcs: durations that are not exact in binary)."""
    stops: list[int] = []
    original_stop = _Trip._stop
    monkeypatch.setattr(_Trip, "_stop", lambda self, *a, **k: (stops.append(1), original_stop(self, *a, **k))[1])
    counts = {}
    for arcs in (30, 300):
        calls: list[int] = []
        original = _Trip._affordable_end
        monkeypatch.setattr(
            _Trip, "_affordable_end", lambda self, limit, calls=calls: (calls.append(limit), original(self, limit))[1]
        )
        graph, nodes = build(
            [(f"P{i}", 1.0 * i, 0.0) for i in range(arcs + 1)], [(f"P{i}", f"P{i + 1}") for i in range(arcs)]
        )
        w = world((graph, nodes), {"A": "P0"}, reserve=False, agv_type=replace(weak(capacity=1e9), idle_power=0.5))
        w.go("A", f"P{arcs}")
        w.env.run()
        assert w.outcomes == {"A": TravelOutcome.ARRIVED}
        assert len(w.of(AgvMoveEnded)) == arcs
        counts[arcs] = len(calls)
        monkeypatch.setattr(_Trip, "_affordable_end", original)
    assert counts[300] == counts[30] <= 3
    assert not stops


@pytest.mark.parametrize("reserve", [False, True])
def test_no_energy_stop_is_raised_while_the_battery_is_ample(reserve: bool) -> None:
    w = world(line(8), {"A": "N0"}, reserve=reserve, agv_type=powered(2.0), lookahead_margin=25.0 if reserve else 0.0)
    w.go("A", "N7")
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.ARRIVED}
    assert len(w.of(AgvMoveEnded)) == 7
    assert w.agvs["A"].battery.level == pytest.approx(1000.0 - 70.0 - 2.0 * w.env.now, rel=1e-12)


@pytest.mark.parametrize("reserve", [False, True])
def test_a_battery_that_idle_power_empties_during_a_run_floors_at_zero_and_is_met_at_the_next_check(
    reserve: bool,
) -> None:
    """Idle power of 20 per second empties the 1000 within the run; the estimate does not foresee it. The level floors
    at 0 and never goes below, nothing raises, and the AGV is met by the existing checks: under free flow the plan is
    the whole route and the AGV arrives empty; under reservation the claims stop being asked for once the level is
    empty and it comes to rest where they end, critical, with a retry. Either way the energy check of its next travel
    finds the empty battery and, with no charger, strands it."""
    kind = replace(weak(capacity=1000.0), idle_power=20.0)
    w = world(line(11), {"A": "N0"}, reserve=reserve, agv_type=kind)
    w.go("A", "N10")
    w.env.run()
    levels = [e.battery for e in w.of(AgvMoveEnded)]
    assert levels == sorted(levels, reverse=True) and min(levels) == 0.0
    assert w.agvs["A"].battery.level == 0.0
    if reserve:
        assert w.outcomes == {"A": TravelOutcome.RETRY_FROM_CURRENT_POSITION}
        assert w.agvs["A"].current_node != w.nodes["N10"]
    else:
        assert w.outcomes == {"A": TravelOutcome.ARRIVED}
        assert w.agvs["A"].current_node == w.nodes["N10"]
    rest = w.agvs["A"].current_node
    w.go("A", "N0" if rest != w.nodes["N0"] else "N1", at=w.env.now + 5.0)
    w.env.run()
    assert w.outcomes == {"A": TravelOutcome.BATTERY_STRANDED}
    assert w.agvs["A"].current_node == rest and w.agvs["A"].state is AGVState.IDLE


# --- charging ----------------------------------------------------------------------------------------------------


def charging_world(power: float, slots: int = 1, swap: bool = False) -> tuple[Environment, FullReplay, StillFleet]:
    graph, nodes = line(2)
    env = Environment(debug=True)
    kind = replace(vehicle(), idle_power=power, battery_capacity=100.0)
    agvs = [AGV(env=env, agv_type=kind, agv_id=name, initial_node=nodes["N0"]) for name in ("A", "B")]
    for agv in agvs:
        agv.battery.level = 50.0
    replay = FullReplay(env, from_snapshot=True)  # after the levels were set: they were not events
    station = ChargingStation(
        env=env,
        name="CS",
        node=nodes["N0"],
        n_slots=slots,
        supports_swap=swap,
        swap_pool_size=1 if swap else 0,
        swap_time=5.0,
    )
    fleet = StillFleet(
        env=env, graph=graph, fleet=agvs, warehouses=[], charging_stations=[station], default_metrics=False
    )
    env.activate()
    return env, replay, fleet


def battery_changes(replay: FullReplay, agv: str) -> list[tuple[float, float]]:
    return [(e.t, e.battery) for e in replay.of(AgvBatteryChanged) if e.agv == agv]


def test_time_spent_charging_does_not_drain_not_even_while_queued_for_a_slot() -> None:
    env, replay, fleet = charging_world(0.5)
    a, b = fleet.fleet
    (station,) = fleet.charging_stations

    def charge(agv: AGV) -> ProcessGenerator:
        yield env.timeout(10.0)
        yield from fleet._charge_agv(agv, station)

    env.process(charge(a))
    env.process(charge(b))
    env.run()
    # Both bring their level up to date at the start of the charge: 50 - 0.5 * 10. A charges for 55 and B, queued in
    # CHARGING for those 55, charges for 55 again: it did not drain meanwhile.
    assert battery_changes(replay, "A") == [(10.0, 45.0), (65.0, 100.0)]
    assert battery_changes(replay, "B") == [(10.0, 45.0), (120.0, 100.0)]
    a.transition_to(AGVState.IDLE)  # the charge is over: time counts again from here
    b.transition_to(AGVState.IDLE)
    env.run(until=env.now + 10.0)
    a.accrue_idle_power()
    b.accrue_idle_power()
    assert (a.battery.level, b.battery.level) == (95.0, 95.0)


def test_a_recharge_outside_the_charging_state_does_not_drain_while_it_charges_but_does_while_it_waits() -> None:
    env, replay, fleet = charging_world(0.5)
    a, b = fleet.fleet
    (station,) = fleet.charging_stations
    env.process(station.recharge(a))
    env.process(station.recharge(b))  # IDLE, queued for the slot until A is done at 50
    env.run()
    assert battery_changes(replay, "A") == [(50.0, 100.0)]
    # B waited 50 in IDLE: 50 - 25; then charges 75 without draining.
    assert battery_changes(replay, "B") == [(50.0, 25.0), (125.0, 100.0)]
    env.run(until=env.now + 10.0)
    a.accrue_idle_power()
    b.accrue_idle_power()
    assert (a.battery.level, b.battery.level) == (57.5, 95.0)  # A went idle at 50, B at 125


def test_a_swap_restores_the_level_and_time_counts_again_from_its_end() -> None:
    env, replay, fleet = charging_world(0.5, swap=True)
    a, _ = fleet.fleet
    (station,) = fleet.charging_stations
    env.process(station.swap(a))
    env.run(until=6.0)
    assert battery_changes(replay, "A") == [(5.0, 100.0)]
    env.run(until=15.0)
    a.accrue_idle_power()
    assert a.battery.level == 95.0


# --- missions and replay -----------------------------------------------------------------------------------------


def mission_world(power: float) -> tuple[Environment, FullReplay, FleetCoordinator, AGV, Any, Any]:
    """``WH_A_OUT - CORRIDOR - WH_B_IN`` with a charging station on ``WH_B_IN``; the AGV unloads for 20."""
    env = Environment(debug=True)
    sku = SKU(id="SKU-A", weight=5.0, volume=0.1)
    a_out, corridor, b_in = Node("WH_A_OUT", 0.0, 0.0), Node("CORRIDOR", 5.0, 0.0), Node("WH_B_IN", 10.0, 0.0)
    graph = LayoutGraph([a_out, corridor, b_in], [Arc(a_out, corridor), Arc(corridor, b_in)])
    wh_a = Warehouse(
        env=env,
        name="WH-A",
        input_bays=[a_out],
        output_bays=[a_out],
        n_slots=2,
        products=[sku],
        initial_inventory={sku: 100},
        pick_time=1.0,
        put_time=1.0,
    )
    wh_b = Warehouse(
        env=env,
        name="WH-B",
        input_bays=[b_in],
        output_bays=[b_in],
        n_slots=2,
        products=[sku],
        initial_inventory={sku: 0},
        pick_time=1.0,
        put_time=1.0,
    )
    kind = AGVType(
        name="test-type",
        speed_profile=TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0),
        battery_capacity=100.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        load_time=3.0,
        unload_time=20.0,
        low_battery_threshold=0.2,
        critical_battery_threshold=0.05,
        idle_power=power,
    )
    agv = AGV(env=env, agv_type=kind, agv_id="AGV-1", initial_node=a_out)
    agv.battery.level = 50.0
    replay = FullReplay(env, from_snapshot=True)  # after the level was set: it was not an event
    charger = ChargingStation(env=env, name="CS-1", node=b_in, n_slots=1)
    fleet = FleetCoordinator(
        env=env, graph=graph, fleet=[agv], warehouses=[wh_a, wh_b], charging_stations=[charger], default_metrics=False
    )
    order = fleet.create_order(sku=sku, quantity=10, origin=wh_a, destination=wh_b)
    env.activate()
    return env, replay, fleet, agv, order, charger


def test_a_mission_with_idle_waits_and_a_charge_replays_the_battery_at_every_event() -> None:
    env, replay, fleet, agv, order, charger = mission_world(1.0)

    def later() -> ProcessGenerator:
        yield env.timeout(10.0)
        fleet.submit(order)

    env.process(later())
    env.run()
    assert order.status is OrderStatus.COMPLETED
    # The idle time before the mission, the pick and the load wait, the unload wait: all accounted as they are read.
    changed = battery_changes(replay, "AGV-1")
    assert changed[0] == (14.0, 36.0)  # 10 idle, the pick (1) and the load (3), found at the energy check
    assert len(replay.of(ChargingStarted)) == 1
    assert charger.total_recharges == 1 and agv.battery.level == 100.0
    assert agv.state is AGVState.IDLE


def test_the_low_battery_check_after_a_mission_finds_the_level_the_unload_wait_drained() -> None:
    """After delivery the level, as last accounted, is above the low threshold; the 21 spent putting and unloading took
    it below. The check brings the level up to date first, so the AGV charges."""
    env, replay, fleet, agv, order, charger = mission_world(1.0)
    fleet.submit(order)
    env.run()
    started = replay.of(ChargingStarted)
    assert len(started) == 1 and charger.total_recharges == 1
    delivered = order.delivered_at
    assert delivered is not None
    assert started[0].t == delivered  # charging begins when the unload is done
    before = [e for e in replay.of(AgvBatteryChanged) if e.agv == "AGV-1" and e.t == delivered]
    assert before and before[0].battery <= 0.2 * 100.0


def test_the_same_mission_without_idle_power_does_not_charge() -> None:
    env, replay, fleet, agv, order, charger = mission_world(0.0)
    fleet.submit(order)
    env.run()
    assert order.status is OrderStatus.COMPLETED
    assert not replay.of(ChargingStarted) and not replay.of(AgvBatteryChanged)
    assert agv.battery.level == pytest.approx(40.0, abs=1e-9)


@pytest.mark.parametrize("reserve", [False, True])
def test_the_affordable_end_does_not_include_the_idle_power_of_the_run_ahead(reserve: bool) -> None:
    """A battery of 25 affords two arcs of 10 at the estimate, with or without idle power: the plan ends at the second
    node, at the same time, either way (spec §9.5). Without idle power the 5 left do not cover another arc and the AGV
    is stranded; with it the level there is 0, critical, and the travel returns for the charging branch."""
    ends = {}
    for power in (0.0, 0.5):
        kind = replace(weak(capacity=25.0), idle_power=power)
        w = world(line(5), {"A": "N0"}, reserve=reserve, agv_type=kind)
        w.go("A", "N4")
        w.env.run()
        node = w.agvs["A"].current_node
        assert node is not None
        ends[power] = (node.id, w.env.now, w.agvs["A"].battery.level, w.outcomes["A"])
    assert ends[0.0][:3] == ("N2", ends[0.5][1], 5.0) and ends[0.0][3] is TravelOutcome.BATTERY_STRANDED
    assert ends[0.5][0] == "N2" and ends[0.5][2:] == (0.0, TravelOutcome.RETRY_FROM_CURRENT_POSITION)
