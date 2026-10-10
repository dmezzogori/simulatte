"""Traffic, warehouse, charging and parking events (spec §6.4, S6): every mutation of ``node``, ``warehouse``,
``charging_station`` and ``parking_area`` state has an event.

Mutation sites (``traffic.py``, ``warehouse.py``, ``charging.py``, ``parking.py``, ``_resources.py``) and the event
whose deltas carry each. ``NotifyingResource`` and ``NotifyingContainer`` (``_resources.py``) call their owner from
SimPy's ``_do_put``/``_do_get``, where SimPy changes ``users`` and ``level``: a request or a get that had to wait is
completed there, in the callback of a later release or put, before the waiting process resumes.

``node`` state

===========  ===========================================================================  =========================
field        site                                                                         event
===========  ===========================================================================  =========================
x, y         ``NodeBinding.__init__``; never changed                                      entity.created
agvs         ``AGV.__init__`` at a bound node, ``current_node`` setter, ``_end_segment``  agv.placed, agv.move_ended
             (Task 15, ``test_fleet_events.py``)
reserved_by  ``place_now`` -> ``_reserve``: initial placement (activation initializer)    traffic.reserved
reserved_by  ``enter_node`` -> ``_reserve``: resumed after the grant                      traffic.reserved
reserved_by  ``leave_node`` -> ``_unreserve``: a granted request is released               traffic.released
reserved_by  ``cancel``: never; it withdraws waiting requests and releases at the          (none; it ends a pending
             resource a grant that ``enter_node`` has not recorded                         wait: traffic.wait_ended)
===========  ===========================================================================  =========================

``FreeTrafficManager`` reserves nothing: ``reserved_by`` stays empty and several AGVs share a node's ``agvs``.

``warehouse`` state

============  ==========================================================================  ===========================
field         site                                                                        event
============  ==========================================================================  ===========================
inventory     container ``_do_get``: ``Warehouse.pick``'s get, at once or when a later    warehouse.inventory_changed
              put is processed
inventory     container ``_do_put``: ``Warehouse.put`` (deliveries, cargo returned to     warehouse.inventory_changed
              the origin, the rollback of a committed pick); any direct container put
inventory     replacing a container in ``inventory`` or writing ``_level`` (user code,    (none: not a framework
              tests)                                                                      transition, ruling R21)
slots_in_use  ``_slots._do_put``: pick or put slot granted, at once or when a release is  warehouse.slot_changed
              processed
slots_in_use  ``_slots._do_get``: the ``with`` block of ``pick``/``put`` exits (also on   warehouse.slot_changed
              an interrupt; not on ``GeneratorExit``, where SimPy keeps the slot)
============  ==========================================================================  ===========================

``charging_station`` state

============  ==========================================================================  ===========================
field         site                                                                        event
============  ==========================================================================  ===========================
slots_in_use  ``_slots._do_put``: the ``_SlotRequest`` of ``recharge``/``swap`` granted    charging.started
slots_in_use  ``_slots._do_get``: the ``finally`` of ``recharge``/``swap`` releases it    charging.ended
swap_pool     ``_swap_pool._do_get``: ``swap`` takes a battery (at once or after a        charging.pool_changed
              replenishment)
swap_pool     ``_swap_pool._do_put``: ``_replenish_pool`` returns it                      charging.pool_changed
============  ==========================================================================  ===========================

The AGV's ``battery`` set by ``recharge`` and ``swap`` is carried by ``agv.battery_changed`` (Task 15).

``parking_area`` state

======  ===============================================================================  ===========================
field   site                                                                             event
======  ===============================================================================  ===========================
parked  ``enter``: ``_agv_requests[agv] = req`` after the grant                         parking.entered
        (an already parked AGV keeps its slot and emits no event)
parked  ``leave``: ``_agv_requests.pop(agv)``                                            parking.left
======  ===============================================================================  ===========================

Waits (no state): ``traffic.wait_started`` / ``traffic.wait_ended`` around the wait of ``enter_node`` (ended by the
grant, ``cancel`` or an interrupt), the ``delay_until`` waits of ``FleetCoordinator._travel`` and the deadlock backoff
of ``_enter_with_timeout``.
"""

from __future__ import annotations

import random
import runpy
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
import simpy

from simulatte.environment import Environment
from simulatte.events import DomainEvent, apply_deltas
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.events import (
    AgvBatteryChanged,
    ChargingEnded,
    ChargingPoolChanged,
    ChargingStarted,
    ParkingEntered,
    ParkingLeft,
    TrafficReleased,
    TrafficReserved,
    TrafficWaitEnded,
    TrafficWaitStarted,
    WarehouseInventoryChanged,
    WarehouseSlotChanged,
)
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.order import OrderStatus
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.policies import ReorderPointPolicy
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import FreeTrafficManager, PathCheckResult, ResourceBasedTrafficManager
from simulatte.intralogistics.warehouse import Warehouse

from tests.intralogistics.test_entities import SKU_A, _agv_type, _line_graph

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

TRAFFIC = (TrafficReserved, TrafficReleased, TrafficWaitStarted, TrafficWaitEnded)


class FullReplay:
    """Applies the deltas of every domain event and compares EVERY live entity, of every kind, after each event.

    Two readers are checked (spec §17, U3): ``state`` replays from the first event seen (from ``{}`` when created
    before any entity, from the live snapshot otherwise), and ``seeded`` replays from ``env.initial_state`` as a
    reader that seeks from the start of the run does. Comparing after every event is comparing at every cursor.
    """

    def __init__(self, env: Environment, *, from_snapshot: bool = False) -> None:
        self.env = env
        self.state: dict[str, dict[str, Any]] = (
            {entity: dict(fields) for entity, fields in env.entities.snapshot().items()} if from_snapshot else {}
        )
        self.seeded: dict[str, dict[str, Any]] | None = None
        self.events: list[DomainEvent] = []
        env.bus.subscribe(self, "*")

    def __call__(self, event: DomainEvent) -> None:
        live = self.env.entities.snapshot()
        apply_deltas(self.state, event.deltas)
        assert self.state == live, event
        initial = self.env._initial_state
        if self.seeded is None and initial is not None:
            self.seeded = {entity: dict(fields) for entity, fields in initial.items()}
        if self.seeded is not None:
            apply_deltas(self.seeded, event.deltas)
            assert self.seeded == live, event
        self.events.append(event)

    def of(self, *types: type[DomainEvent]) -> list[Any]:
        return [event for event in self.events if isinstance(event, types)]


def _bound_line(env: Environment) -> tuple[list[Node], LayoutGraph]:
    nodes, graph = _line_graph()
    for node in nodes:
        env.entities.bind_node(node)
    return nodes, graph


# --- traffic -----------------------------------------------------------------------------------------------


def test_node_capacity_two_reservations() -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    nodes, graph = _bound_line(env)
    _, c1, c2, _ = nodes
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=2)
    a, b, c = (AGV(env=env, agv_type=_agv_type(), agv_id=name, initial_node=c2) for name in ("A", "B", "C"))
    traffic.place_now(a, c1)
    traffic.place_now(b, c1)  # capacity two: both hold C1
    with pytest.raises(RuntimeError, match="fully reserved"):
        traffic.place_now(c, c1)
    assert env.entities.bind_node(c1).reserved_by == ["A", "B"]

    def enter_late() -> Any:
        yield from traffic.enter_node(c, c1)

    def leave_first() -> Any:
        yield env.timeout(2.0)
        traffic.leave_node(a, c1)
        traffic.leave_node(a, c1)  # nothing left to release: no event

    env.process(enter_late())
    env.process(leave_first())
    env.run()

    seen = [(e.t, e.type_name, e.agv, e.node, getattr(e, "reason", None)) for e in replay.of(*TRAFFIC)]
    assert seen == [
        (0, "traffic.reserved", "A", "C1", None),
        (0, "traffic.reserved", "B", "C1", None),
        (0, "traffic.wait_started", "C", "C1", "node_occupied"),
        (2, "traffic.released", "A", "C1", None),
        (2, "traffic.wait_ended", "C", "C1", "granted"),
        (2, "traffic.reserved", "C", "C1", None),
    ]
    reserved = replay.of(TrafficReserved)
    assert [e.deltas.ops for e in reserved] == [
        (("insert", "C1", "reserved_by", 0, "A"),),
        (("insert", "C1", "reserved_by", 1, "B"),),
        (("insert", "C1", "reserved_by", 1, "C"),),
    ]
    assert replay.of(TrafficReleased)[0].deltas.ops == (("remove", "C1", "reserved_by", "A"),)
    assert replay.state["C1"]["reserved_by"] == ("B", "C")


def test_free_traffic_multiple_agvs_on_node() -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    nodes, graph = _line_graph()
    wh_a, wh_b = (
        Warehouse(
            env=env,
            name=name,
            input_bays=[bay],
            output_bays=[bay],
            n_slots=1,
            products=[SKU_A],
            initial_inventory={SKU_A: level},
            pick_time=1.0,
            put_time=1.0,
        )
        for name, bay, level in (("WH-A", nodes[0], 10), ("WH-B", nodes[-1], 0))
    )
    agvs = [AGV(env=env, agv_type=_agv_type(), initial_node=nodes[1]) for _ in range(3)]
    coordinator = FleetCoordinator(
        env=env, graph=graph, fleet=agvs, warehouses=[wh_a, wh_b], charging_stations=[], traffic_manager=None
    )
    assert isinstance(coordinator._traffic_manager, FreeTrafficManager)
    shared: list[int] = []  # after each event: the most AGVs on one node (replay is subscribed first)
    env.bus.subscribe(
        lambda e: shared.append(
            max((len(f["agvs"]) for f in replay.state.values() if f["$kind"] == "node"), default=0)
        ),
        "*",
    )
    for _ in range(3):
        coordinator.submit(coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b))
    env.run()

    assert env.initial_state["C1"]["agvs"] == tuple(agv.id for agv in agvs)
    assert all(fields.get("reserved_by", ()) == () for fields in replay.state.values())
    assert replay.of(*TRAFFIC) == []
    assert shared[-1] == 3  # all three delivered to IN: one node, three AGVs
    assert sorted(replay.state["IN"]["agvs"]) == sorted(agv.id for agv in agvs)
    assert replay.state["WH-B"]["inventory"]["SKU-A"] == 3.0


def test_enter_wait_ended_by_cancel_and_by_interrupt() -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    nodes, graph = _bound_line(env)
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)
    holder, cancelled, interrupted = (
        AGV(env=env, agv_type=_agv_type(), agv_id=name, initial_node=nodes[0]) for name in ("H", "X", "Y")
    )
    traffic.place_now(holder, nodes[1])
    env.process(traffic.enter_node(cancelled, nodes[1]))
    waiting = env.process(traffic.enter_node(interrupted, nodes[1]))
    env.run(until=1.0)
    traffic.cancel(cancelled)
    traffic.cancel(cancelled)  # no wait left open: nothing more
    waiting.interrupt("deadlock_timeout")
    env.run(until=2.0)

    seen = [(e.t, e.type_name, e.agv, e.reason) for e in replay.of(TrafficWaitStarted, TrafficWaitEnded)]
    assert seen == [
        (0, "traffic.wait_started", "X", "node_occupied"),
        (0, "traffic.wait_started", "Y", "node_occupied"),
        (1, "traffic.wait_ended", "X", "cancelled"),
        (1, "traffic.wait_ended", "Y", "interrupted"),
    ]
    assert traffic._waiting == {}
    assert replay.state["C1"]["reserved_by"] == ("H",)


def _fleet_on(env: Environment, graph: LayoutGraph, start: Node, origin: Node, destination: Node, **options: Any):
    def warehouse(name: str, bay: Node, level: int) -> Warehouse:
        return Warehouse(
            env=env,
            name=name,
            input_bays=[bay],
            output_bays=[bay],
            n_slots=2,
            products=[SKU_A],
            initial_inventory={SKU_A: level},
            pick_time=1.0,
            put_time=1.0,
        )

    wh_a, wh_b = warehouse("WH-A", origin, 10), warehouse("WH-B", destination, 0)
    agv = AGV(env=env, agv_type=_agv_type(), agv_id="T", initial_node=start)
    coordinator = FleetCoordinator(
        env=env, graph=graph, fleet=[agv], warehouses=[wh_a, wh_b], charging_stations=[], **options
    )
    order = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(order)
    return coordinator, order


class _DelayingTraffic(FreeTrafficManager):
    """Path checks: a conflict on the first path, a delay of 3 for the alternative, then a delay of 2."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.calls = 0

    def check_path(self, agv: AGV, path: list[Node]) -> PathCheckResult:
        self.calls += 1
        now = self.env.now
        return {
            1: PathCheckResult(feasible=False, conflict_nodes=[path[1]]),
            2: PathCheckResult(feasible=False, delay_until=now + 3.0),
            3: PathCheckResult(feasible=False, delay_until=now + 2.0),
        }.get(self.calls, PathCheckResult(feasible=True))


def _diamond() -> tuple[dict[str, Node], LayoutGraph]:
    nodes = {n.id: n for n in (Node("A", 0.0, 0.0), Node("M", 5.0, 1.0), Node("N", 5.0, -2.0), Node("B", 10.0, 0.0))}
    a, m, n, b = nodes.values()
    return nodes, LayoutGraph(nodes.values(), [Arc(a, m), Arc(m, b), Arc(a, n), Arc(n, b)])


@pytest.mark.parametrize("interrupt", [False, True])
def test_path_delay_waits(interrupt: bool) -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    nodes, graph = _diamond()
    coordinator, order = _fleet_on(
        env, graph, nodes["A"], nodes["A"], nodes["B"], traffic_manager=_DelayingTraffic(env)
    )
    if interrupt:
        env.run(until=3.0)  # loaded at 2, waiting for the alternative path
        coordinator._active_missions[order.id].interrupt("breakdown")
    env.run()

    seen = [(e.t, e.type_name, e.node, e.reason) for e in replay.of(TrafficWaitStarted, TrafficWaitEnded)]
    if interrupt:
        assert seen == [
            (2, "traffic.wait_started", "N", "path_delay"),
            (3, "traffic.wait_ended", "N", "interrupted"),
            (6, "traffic.wait_started", "M", "path_delay"),
            (8, "traffic.wait_ended", "M", "elapsed"),
        ]
        assert order.status is OrderStatus.COMPLETED
    else:
        # First the alternative's delay, then the delay of the same path checked again (spec §6.4 "reroute delays").
        assert seen == [
            (2, "traffic.wait_started", "N", "path_delay"),
            (5, "traffic.wait_ended", "N", "elapsed"),
            (5, "traffic.wait_started", "N", "path_delay"),
            (7, "traffic.wait_ended", "N", "elapsed"),
        ]
        assert order.status is OrderStatus.COMPLETED


def test_deadlock_wait_and_backoff() -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    start, choke, end = Node("START", 0.0, 0.0), Node("CHOKE", 5.0, 0.0), Node("END", 10.0, 0.0)
    graph = LayoutGraph([start, choke, end], [Arc(start, choke), Arc(choke, end)])
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1, deadlock_timeout=2.0)
    blocker = AGV(env=env, agv_type=_agv_type(), agv_id="blocker", initial_node=choke)
    traveler = AGV(env=env, agv_type=_agv_type(), agv_id="traveler", initial_node=start)
    wh_a, wh_b = (
        Warehouse(
            env=env,
            name=name,
            input_bays=[bay],
            output_bays=[bay],
            n_slots=2,
            products=[SKU_A],
            initial_inventory={SKU_A: 5},
            pick_time=1.0,
            put_time=1.0,
        )
        for name, bay in (("WH-S", start), ("WH-E", end))
    )
    coordinator = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=[traveler, blocker],
        warehouses=[wh_a, wh_b],
        charging_stations=[],
        traffic_manager=traffic,
    )
    order = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(order)
    env.run(until=100.0)

    assert order.status is OrderStatus.FAILED
    seen = [(e.t, e.type_name, e.agv, e.node, e.reason) for e in replay.of(TrafficWaitStarted, TrafficWaitEnded)]
    wait, backoff = "node_occupied", "deadlock_backoff"
    # Three attempts, each a 2.0 wait for CHOKE cancelled at the timeout, then a backoff of 2, 4 and 8.
    expected: list[tuple[float, str, str, str, str]] = []
    t = 2.0  # loaded at the origin bay: pick 1.0, load 1.0
    for multiplier in (1, 2, 4):
        expected += [
            (t, "traffic.wait_started", "traveler", "CHOKE", wait),
            (t + 2.0, "traffic.wait_ended", "traveler", "CHOKE", "cancelled"),
            (t + 2.0, "traffic.wait_started", "traveler", "CHOKE", backoff),
            (t + 2.0 + 2.0 * multiplier, "traffic.wait_ended", "traveler", "CHOKE", "elapsed"),
        ]
        t += 2.0 + 2.0 * multiplier
    assert seen == expected
    assert replay.state["CHOKE"]["reserved_by"] == ("blocker",)
    assert replay.state["START"]["reserved_by"] == ("traveler",)


# --- warehouses --------------------------------------------------------------------------------------------


def test_inventory_levels_replay() -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    node = Node("BAY", 0.0, 0.0)
    wh = Warehouse(
        env=env,
        name="WH",
        input_bays=[node],
        output_bays=[node],
        n_slots=1,
        products=[SKU_A],
        initial_inventory={SKU_A: 1},
        pick_time=2.0,
        put_time=1.0,
    )
    noise: list[float] = []

    def pick(quantity: int) -> Any:
        yield from wh.pick(SKU_A, quantity)

    def put() -> Any:
        yield env.timeout(0.5)
        yield from wh.put(SKU_A, 3)

    def same_instant() -> Any:
        # Asks for the slot at t=3: when the put releases it, the slot events of t=3 come after the waiting get was
        # completed and before its process resumes, so the get's event must not wait for that process.
        yield env.timeout(3.0)
        noise.append(env.now)
        yield from wh.put(SKU_A, 1)

    env.process(pick(1))  # t=0: takes the only unit and the only slot until t=2
    env.process(pick(2))  # waits for stock
    env.process(put())  # waits for the slot until t=2, puts at t=3
    env.process(same_instant())
    env.run()

    inventory = [(e.t, e.level, e.delta) for e in replay.of(WarehouseInventoryChanged)]
    assert inventory == [(0, 0.0, -1.0), (3, 3.0, 3.0), (3, 1.0, -2.0), (4, 2.0, 1.0)]
    assert all(isinstance(e.level, float) and isinstance(e.delta, float) for e in replay.of(WarehouseInventoryChanged))
    slots = [(e.t, e.in_use) for e in replay.of(WarehouseSlotChanged)]
    assert slots == [(0, 1), (2, 0), (2, 1), (3, 0), (3, 1), (4, 0), (4, 1), (6, 0)]
    assert replay.of(WarehouseInventoryChanged)[1].deltas.ops == (("put", "WH", "inventory", "SKU-A", 3.0),)
    assert replay.of(WarehouseSlotChanged)[0].deltas.ops == (("set", "WH", "slots_in_use", 1),)
    assert replay.state["WH"]["inventory"] == {"SKU-A": 2.0} and noise == [3.0]
    assert wh.get_inventory_level(SKU_A) == 2.0


def test_slot_request_withdrawn_while_waiting_emits_nothing() -> None:
    """A put interrupted while it waits for a slot leaves its ``with`` block: SimPy releases a request that holds no
    slot, which changes nothing and emits nothing."""
    env = Environment(debug=True)
    replay = FullReplay(env)
    node = Node("BAY", 0.0, 0.0)
    wh = Warehouse(
        env=env,
        name="WH",
        input_bays=[node],
        output_bays=[node],
        n_slots=1,
        products=[SKU_A],
        pick_time=1.0,
        put_time=2.0,
    )

    def put() -> Any:
        try:
            yield from wh.put(SKU_A, 1)
        except simpy.Interrupt:
            pass

    env.process(put())
    waiting = env.process(put())
    env.run(until=1.0)
    waiting.interrupt()
    env.run()
    assert [(e.t, e.in_use) for e in replay.of(WarehouseSlotChanged)] == [(0, 1), (2, 0)]
    assert [(e.t, e.level) for e in replay.of(WarehouseInventoryChanged)] == [(2, 1.0)]


def test_container_built_like_simpy_reports_nothing() -> None:
    """A container made with ``type(container)(env, ...)`` (as tests do) has no owner and emits nothing."""
    env = Environment(debug=True)
    seen: list[DomainEvent] = []
    env.bus.subscribe(seen.append, (WarehouseInventoryChanged,))
    nodes, _ = _line_graph()
    wh = Warehouse(
        env=env,
        name="WH",
        input_bays=[nodes[0]],
        output_bays=[nodes[0]],
        n_slots=1,
        products=[SKU_A],
        pick_time=1.0,
        put_time=1.0,
    )
    loose = type(wh.inventory[SKU_A])(env=env, capacity=10, init=5)
    loose.get(2)
    loose.put(4)
    env.run()
    assert loose.level == 7 and seen == []


# --- charging ----------------------------------------------------------------------------------------------


def _drained(env: Environment, name: str, level: float) -> AGV:
    agv = AGV(env=env, agv_type=_agv_type(), agv_id=name)
    agv.battery.level = level  # a user write: before the replay starts
    return agv


def test_swap_updates_pool_and_battery() -> None:
    env = Environment(debug=True)
    nodes, _ = _line_graph()
    station = ChargingStation(
        env=env,
        name="CS",
        node=nodes[0],
        n_slots=2,
        supports_swap=True,
        swap_pool_size=1,
        swap_time=1.0,
        swap_recharge_time=10.0,
    )
    first, second = _drained(env, "A", 100.0), _drained(env, "B", 200.0)
    replay = FullReplay(env, from_snapshot=True)
    env.process(station.swap(first))
    env.process(station.swap(second))  # waits for the pool
    env.run()

    seen = [
        (e.t, e.type_name, getattr(e, "agv", None), getattr(e, "mode", None), getattr(e, "swap_pool", None))
        for e in replay.of(ChargingStarted, ChargingEnded, ChargingPoolChanged, AgvBatteryChanged)
    ]
    assert seen == [
        (0, "charging.started", "A", "swap", None),
        (0, "charging.started", "B", "swap", None),
        (0, "charging.pool_changed", None, None, 0.0),  # taken by A; B waits for the pool
        (1, "agv.battery_changed", "A", None, None),
        (1, "charging.ended", "A", "swap", None),
        (11, "charging.pool_changed", None, None, 1.0),  # replenished
        (11, "charging.pool_changed", None, None, 0.0),  # taken by B, before B resumes
        (12, "agv.battery_changed", "B", None, None),
        (12, "charging.ended", "B", "swap", None),
        (22, "charging.pool_changed", None, None, 1.0),
    ]
    in_use = [e.deltas.ops for e in replay.of(ChargingStarted, ChargingEnded)]
    assert in_use == [(("set", "CS", "slots_in_use", n),) for n in (1, 2, 1, 0)]
    assert replay.state["A"]["battery"] == replay.state["B"]["battery"] == 1000.0
    assert replay.state["CS"]["swap_pool"] == 1.0 and replay.state["CS"]["slots_in_use"] == 0
    assert station.total_swaps == 2


def test_recharge_slots_and_interruption() -> None:
    env = Environment(debug=True)
    nodes, _ = _line_graph()
    station = ChargingStation(
        env=env, name="CS", node=nodes[0], n_slots=1, recharge_time=lambda level, target: (target - level) / 100.0
    )
    agvs = [_drained(env, name, 500.0) for name in ("A", "B", "C")]
    replay = FullReplay(env, from_snapshot=True)

    def charge(agv: AGV) -> Any:
        try:
            yield from station.recharge(agv)
        except simpy.Interrupt:
            pass

    procs = [env.process(charge(agv)) for agv in agvs]
    env.run(until=6.0)
    procs[1].interrupt("breakdown")  # B charges from 5 to 10: its slot is released early
    env.run()

    seen = [(e.t, e.type_name, e.agv) for e in replay.of(ChargingStarted, ChargingEnded, AgvBatteryChanged)]
    assert seen == [
        (0, "charging.started", "A"),
        (5, "agv.battery_changed", "A"),
        (5, "charging.ended", "A"),
        (5, "charging.started", "B"),
        (6, "charging.ended", "B"),
        (6, "charging.started", "C"),
        (11, "agv.battery_changed", "C"),
        (11, "charging.ended", "C"),
    ]
    assert {e.mode for e in replay.of(ChargingStarted, ChargingEnded)} == {"recharge"}
    assert replay.state["B"]["battery"] == 500.0 and replay.state["CS"]["slots_in_use"] == 0
    assert replay.of(ChargingPoolChanged) == []


# --- parking -----------------------------------------------------------------------------------------------


def test_parking_enter_and_leave() -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    nodes, _ = _line_graph()
    area = ParkingArea(env=env, name="PARK", node=nodes[0], capacity=1)
    a, b = (AGV(env=env, agv_type=_agv_type(), agv_id=name) for name in ("A", "B"))

    def visit(agv: AGV, stay: float) -> Any:
        yield from area.enter(agv)
        yield env.timeout(stay)
        area.leave(agv)

    env.process(visit(a, 2.0))
    env.process(visit(b, 1.0))  # waits for A to leave
    env.run()
    with pytest.raises(KeyError):
        area.leave(a)  # not parked: nothing changes

    seen = [(e.t, e.type_name, e.agv, e.deltas.ops) for e in replay.of(ParkingEntered, ParkingLeft)]
    assert seen == [
        (0, "parking.entered", "A", (("insert", "PARK", "parked", 0, "A"),)),
        (2, "parking.left", "A", (("remove", "PARK", "parked", "A"),)),
        (2, "parking.entered", "B", (("insert", "PARK", "parked", 0, "B"),)),
        (3, "parking.left", "B", (("remove", "PARK", "parked", "B"),)),
    ]


def test_parking_enter_again_keeps_the_place() -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    nodes, _ = _line_graph()
    area = ParkingArea(env=env, name="PARK", node=nodes[0], capacity=3)
    a, b = (AGV(env=env, agv_type=_agv_type(), agv_id=name) for name in ("A", "B"))

    def park(*agvs: AGV) -> Any:
        for agv in agvs:
            yield from area.enter(agv)

    env.process(park(a, b, a))
    env.run()
    assert [e.deltas.ops for e in replay.of(ParkingEntered)] == [
        (("insert", "PARK", "parked", 0, "A"),),
        (("insert", "PARK", "parked", 1, "B"),),
    ]
    assert replay.state["PARK"]["parked"] == ("A", "B")
    assert area.available_capacity == 1


# --- replay over full systems ------------------------------------------------------------------------------


def _run_advanced_example(monkeypatch: pytest.MonkeyPatch) -> FullReplay:
    """Run ``examples/intralogistics_advanced.py`` unchanged, with its environment in debug mode and replayed."""
    plotting = find_spec("matplotlib") is not None
    if plotting:
        import matplotlib.pyplot

    import simulatte.environment

    replays: list[FullReplay] = []

    class Checked(Environment):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, debug=True, **kwargs)
            replays.append(FullReplay(self))

    if plotting:
        monkeypatch.setattr(matplotlib.pyplot, "show", lambda: None)
    monkeypatch.setattr(simulatte.environment, "Environment", Checked)
    example_globals = runpy.run_path(
        str(EXAMPLES / "intralogistics_advanced.py"), run_name="__main__" if plotting else "__headless__"
    )
    if not plotting:
        example_globals["main"](plot=False)
    (replay,) = replays
    return replay


def _run_congested(monkeypatch: pytest.MonkeyPatch) -> FullReplay:
    """A grid with node capacity one, contended warehouse slots, battery swaps and recharges, parking and
    interruptions."""
    env = Environment(seed=11, debug=True)
    replay = FullReplay(env)
    rng = random.Random(7)
    grid = {(i, j): Node(f"N{i}{j}", 10.0 * i, 10.0 * j) for i in range(3) for j in range(3)}
    arcs = [Arc(grid[i, j], grid[i + 1, j]) for i in range(2) for j in range(3)]
    arcs += [Arc(grid[i, j], grid[i, j + 1]) for i in range(3) for j in range(2)]
    graph = LayoutGraph(grid.values(), arcs)
    skus = [SKU_A]

    def warehouse(name: str, node: Node, level: int) -> Warehouse:
        return Warehouse(
            env=env,
            name=name,
            input_bays=[node],
            output_bays=[node],
            n_slots=1,
            products=skus,
            initial_inventory={SKU_A: level},
            pick_time=4.0,
            put_time=3.0,
        )

    source, sink, buffer = (
        warehouse("SRC", grid[0, 0], 12),
        warehouse("SNK", grid[2, 2], 0),
        warehouse("BUF", grid[2, 0], 4),
    )
    agv_type = AGVType(
        name="small",
        speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
        battery_capacity=60.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        depletion_fn=lambda distance, load, speed: distance * 0.5,
        low_battery_threshold=0.4,
        critical_battery_threshold=0.1,
        load_time=2.0,
        unload_time=2.0,
    )
    starts = [grid[1, 1], grid[0, 2], grid[1, 0], grid[2, 1]]
    agvs = [AGV(env=env, agv_type=agv_type, agv_id=f"V{k}", initial_node=node) for k, node in enumerate(starts)]
    swap = ChargingStation(
        env=env,
        name="SWAP",
        node=grid[0, 1],
        n_slots=1,
        supports_swap=True,
        swap_pool_size=1,
        swap_time=2.0,
        swap_recharge_time=40.0,
    )
    plug = ChargingStation(env=env, name="PLUG", node=grid[1, 2], n_slots=1)
    area = ParkingArea(env=env, name="LOT", node=grid[1, 2], capacity=1)
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=2, deadlock_timeout=6.0)

    def low_battery(agv: AGV) -> Any:
        return swap.swap(agv) if agv.agv_id in ("V0", "V2") else None  # the others recharge at PLUG

    coordinator = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=agvs,
        warehouses=[source, sink, buffer],
        charging_stations=[plug],
        parking_areas=[area],
        traffic_manager=traffic,
        on_low_battery=low_battery,
    )
    coordinator.add_replenishment_policy(ReorderPointPolicy({SKU_A: 3}, {SKU_A: 4}), source, check_interval=30.0)

    def orders() -> Any:
        while True:
            yield env.timeout(rng.uniform(3.0, 9.0))
            origin, destination = rng.choice([(source, sink), (buffer, sink), (source, buffer)])
            order = coordinator.create_order(sku=SKU_A, quantity=1, origin=origin, destination=destination)
            coordinator.submit(order)
            if rng.random() < 0.1:
                coordinator.cancel(order)

    def disruptions() -> Any:
        while True:
            yield env.timeout(rng.uniform(15.0, 30.0))
            missions = [mission for mission in coordinator._active_missions.values() if mission.is_alive]
            if missions:
                rng.choice(missions).interrupt("breakdown")

    def parking(agv: AGV, offset: float) -> Any:
        yield env.timeout(offset)
        while True:
            yield from area.enter(agv)
            yield env.timeout(5.0)
            area.leave(agv)
            yield env.timeout(rng.uniform(1.0, 4.0))

    env.process(orders())
    env.process(disruptions())
    for k, agv in enumerate(agvs[:2]):
        env.process(parking(agv, 1.0 + k))
    env.run(until=1000.0)
    return replay


@pytest.mark.parametrize("scenario", [_run_advanced_example, _run_congested], ids=["advanced_example", "congested"])
def test_replay_equals_live_full_registry_fleet_example(scenario: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = scenario(monkeypatch)  # FullReplay asserted every entity of every kind after every event

    types = {event.type_name for event in replay.events}
    expected = {
        "warehouse.inventory_changed",
        "warehouse.slot_changed",
        "charging.started",
        "charging.ended",
        "agv.battery_changed",
        "agv.move_ended",
        "order.status_changed",
    }
    if scenario is _run_congested:
        expected |= {
            "traffic.reserved",
            "traffic.released",
            "traffic.wait_started",
            "traffic.wait_ended",
            "charging.pool_changed",
            "parking.entered",
            "parking.left",
            "agv.move_interrupted",
        }
    assert expected <= types, expected - types
    assert replay.seeded is not None and replay.seeded == replay.state
    assert len(replay.events) > 1000


@pytest.mark.parametrize("mode", ["recharge", "swap"])
@pytest.mark.parametrize("phase", ["queued", "granted", "active"])
def test_charging_interruption_replays_resource_cleanup(mode: str, phase: str) -> None:
    env = Environment(debug=True)
    nodes, _ = _line_graph()
    station = ChargingStation(
        env=env,
        name="CS",
        node=nodes[0],
        n_slots=1,
        recharge_time=10.0,
        supports_swap=True,
        swap_pool_size=1,
        swap_time=10.0,
    )
    holder, waiter = (_drained(env, name, 500.0) for name in ("H", "W"))
    replay = FullReplay(env, from_snapshot=True)
    operation = station.recharge if mode == "recharge" else station.swap

    def attempt() -> Any:
        try:
            yield from operation(waiter)
        except simpy.Interrupt:
            pass

    if phase == "queued":
        env.process(station.recharge(holder))
    process = env.process(attempt())
    env.activate()
    if phase == "granted":
        env.step()
    else:
        env.run(until=1.0)
    process.interrupt("breakdown")
    env.run()
    started = [event.agv for event in replay.of(ChargingStarted)]
    ended = [event.agv for event in replay.of(ChargingEnded)]
    assert started == ended == (["H"] if phase == "queued" else ["W"])
    assert replay.state["CS"]["slots_in_use"] == 0
    assert replay.state["CS"]["swap_pool"] == 1.0
    assert replay.state["W"]["battery"] == 500.0


@pytest.mark.parametrize("granted", [False, True])
def test_traffic_interruption_replays_without_false_reservation(granted: bool) -> None:
    env = Environment(debug=True)
    replay = FullReplay(env)
    nodes, graph = _bound_line(env)
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, deadlock_timeout=None)
    holder, waiter = (AGV(env=env, agv_type=_agv_type(), agv_id=name) for name in ("H", "W"))
    if not granted:
        traffic.place_now(holder, nodes[1])
    env.run()

    def attempt() -> Any:
        with pytest.raises(simpy.Interrupt):
            yield from traffic.enter_node(waiter, nodes[1])

    process = env.process(attempt())
    env.step()
    process.interrupt("cancelled")
    env.run()
    assert replay.state["C1"]["reserved_by"] == (() if granted else ("H",))
    assert all(event.agv != "W" for event in replay.of(TrafficReserved, TrafficReleased))
    assert [event.reason for event in replay.of(TrafficWaitEnded)] == ([] if granted else ["interrupted"])
