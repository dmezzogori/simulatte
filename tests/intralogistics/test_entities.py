"""Intralogistics entities: kinds, node bindings, deterministic ordering and order retirement (spec §5)."""

from __future__ import annotations

import dataclasses
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from simulatte.entities import EntityCreated, EntityRetired
from simulatte.environment import Environment
from simulatte.events import DomainEvent, apply_deltas
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node, NodeBinding
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.policies import ReorderPointPolicy
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.warehouse import Warehouse
from simulatte.layout import LayoutError

from tests.intralogistics.traffic_support import World, declare

SKU_A = SKU(id="SKU-A", weight=1.0, volume=0.1)


def _agv_type() -> AGVType:
    return AGVType(
        name="test",
        speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
        battery_capacity=1000.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        load_time=1.0,
        unload_time=1.0,
    )


def _line_graph() -> tuple[list[Node], LayoutGraph]:
    nodes = [Node(id=name, x=5.0 * i, y=0.0) for i, name in enumerate(["OUT", "C1", "C2", "IN"])]
    arcs = [Arc(source=a, target=b) for a, b in zip(nodes, nodes[1:], strict=False)]
    return nodes, LayoutGraph(nodes, arcs)


def _warehouses(env: Environment, world: World) -> tuple[Warehouse, Warehouse]:
    """WH-A on OUT, stocked, and WH-B on IN, empty."""

    def make(name: str, bay: str, level: int) -> Warehouse:
        return Warehouse(
            env=env,
            name=name,
            input_bays=[world.ref(bay)],
            output_bays=[world.ref(bay)],
            n_slots=2,
            products=[SKU_A],
            initial_inventory={SKU_A: level},
            pick_time=1.0,
            put_time=1.0,
        )

    return make("WH-A", "OUT", 100), make("WH-B", "IN", 0)


def _system(
    env: Environment, *, n_agvs: int = 1, resource_traffic: bool = False
) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, World]:
    """A line OUT - C1 - C2 - IN declared on the layout of `env`, with WH-A at OUT and WH-B at IN; AGVs start at
    C1, C2, ..."""
    world = declare(env, _line_graph()[1])
    wh_a, wh_b = _warehouses(env, world)
    agv_type = dataclasses.replace(_agv_type(), length=1.0) if resource_traffic else _agv_type()
    starts = ["C1", "C2", "IN"]
    agvs = [AGV(env=env, agv_type=agv_type, initial_node=world.ref(starts[i])) for i in range(n_agvs)]
    traffic = ReservationController(graph=world.graph, env=env) if resource_traffic else None
    coordinator = FleetCoordinator(
        env=env,
        graph=world.graph,
        fleet=agvs,
        warehouses=[wh_a, wh_b],
        charging_stations=[],
        traffic_controller=traffic,
    )
    return coordinator, agvs, wh_a, wh_b, world


class ReplayChecker:
    """Applies the deltas of every domain event; compares the replayed entity ids and kinds with the registry.

    Only the lifecycle is compared: fleet, AGV, order and resource events arrive with Tasks 15 and 16.
    """

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.state: dict[str, dict[str, Any]] = {}
        self.checked: list[str] = []
        env.bus.subscribe(self, "*")

    def kinds(self) -> dict[str, Any]:
        return {entity_id: fields["$kind"] for entity_id, fields in self.state.items()}

    def __call__(self, event: DomainEvent) -> None:
        apply_deltas(self.state, event.deltas)
        live = {entity.id: entity.kind for entity in self.env.entities.live()}
        assert self.kinds() == live, event
        self.checked.append(event.type_name)


# --- kinds and snapshots -----------------------------------------------------------------------------------


def test_kinds_ids_and_initial_state() -> None:
    env = Environment(debug=True)  # validates every create delta against its kind's schema
    created: list[EntityCreated] = []
    env.bus.subscribe(created.append, (EntityCreated,))
    coordinator, (agv,), wh_a, wh_b, world = _system(env)
    ChargingStation(env=env, name="CS", node=world.ref("C2"), n_slots=1, supports_swap=True, swap_pool_size=2)
    ParkingArea(env=env, name="PARK", node=world.ref("C1"), capacity=2)
    order = coordinator.create_order(sku=SKU_A, quantity=3, origin=wh_a, destination=wh_b)
    env.activate()

    # The layout binds the nodes at preparation, after the components, in id order.
    assert [(e.kind, e.entity) for e in created] == [
        ("warehouse", "WH-A"),
        ("warehouse", "WH-B"),
        ("agv", "agv-0"),
        ("fleet", "fleet-0"),
        ("charging_station", "CS"),
        ("parking_area", "PARK"),
        ("order", "order-0"),
        ("node", "C1"),
        ("node", "C2"),
        ("node", "IN"),
        ("node", "OUT"),
    ]
    assert (agv.id, agv.agv_id, coordinator.id, order.id) == ("agv-0", "agv-0", "fleet-0", "order-0")

    state = env.initial_state
    assert state["agv-0"] == {
        "$kind": "agv",
        "node": "C1",
        "state": "IDLE",
        "battery": 1000.0,
        "load": None,
        "order": None,
        "motion": None,
        "fleet": "fleet-0",
        "heading": None,
        "wait": None,
        "route": None,
        "battery_capacity": 1000.0,
        "label": "agv-0",
    }
    assert state["order-0"] == {
        "$kind": "order",
        "status": "PENDING",
        "sku": "SKU-A",
        "quantity": 3,
        "origin": "WH-A",
        "destination": "WH-B",
        "agv": None,
        "created_at": 0.0,
        "dispatched_at": None,
        "picked_at": None,
        "delivered_at": None,
        "fleet": "fleet-0",
        "label": "order-0",
    }
    assert state["fleet-0"] == {
        "$kind": "fleet",
        "pending": (),
        "waits": 0,
        "stuck_waits": 0,
        "deadlocks": 0,
        "label": "fleet-0",
    }
    assert state["WH-A"] == {"$kind": "warehouse", "inventory": {"SKU-A": 100.0}, "slots_in_use": 0, "label": "WH-A"}
    assert state["CS"] == {"$kind": "charging_station", "slots_in_use": 0, "swap_pool": 2.0, "slots": 1, "label": "CS"}
    assert state["PARK"] == {"$kind": "parking_area", "parked": (), "capacity": 2, "label": "PARK"}
    assert state["C1"] == {"$kind": "node", "x": 5.0, "y": 0.0, "agvs": ("agv-0",), "reserved_by": (), "label": "C1"}

    agv.current_load = {SKU_A: 2}
    assert agv.snapshot()["load"] == {"SKU-A": 2}


def test_facility_snapshots_follow_their_resources() -> None:
    env = Environment()
    world = declare(env, _line_graph()[1])
    station = ChargingStation(env=env, name="CS", node=world.ref("OUT"), n_slots=1, label="Charger")
    area = ParkingArea(env=env, name="PARK", node=world.ref("OUT"), capacity=2)
    agvs = [AGV(env=env, agv_type=_agv_type(), agv_id=name, initial_node=world.ref("OUT")) for name in ("B", "A")]

    def park() -> Any:
        for agv in agvs:
            yield from area.enter(agv)

    env.process(park())
    env.run()
    assert station.snapshot() == {"slots_in_use": 0, "swap_pool": None, "slots": 1, "label": "Charger"}
    assert area.snapshot()["parked"] == ["B", "A"]  # arrival order
    area.leave(agvs[0])
    assert area.snapshot()["parked"] == ["A"]


def test_reserved_agv_names_rejected_and_custom_names_kept() -> None:
    env = Environment()
    with pytest.raises(ValueError, match="reserved"):
        AGV(env=env, agv_type=_agv_type(), agv_id="agv-7")
    world = declare(env, _line_graph()[1])
    agv = AGV(env=env, agv_type=_agv_type(), agv_id="forklift", label="Forklift A")
    fleet = FleetCoordinator(env=env, graph=world.graph, fleet=[agv], warehouses=[], charging_stations=[], name="yard")
    assert (agv.id, agv.label, fleet.id) == ("forklift", "Forklift A", "yard")


# --- node bindings -----------------------------------------------------------------------------------------


def test_shared_graph_two_fleets_one_binding_per_node() -> None:
    env = Environment()
    world = declare(env, _line_graph()[1])
    first = AGV(env=env, agv_type=_agv_type(), initial_node=world.ref("C1"))
    fleet_a = FleetCoordinator(env=env, graph=world.graph, fleet=[first], warehouses=[], charging_stations=[])
    second = AGV(env=env, agv_type=_agv_type(), initial_node=world.ref("C1"))  # created after the first fleet
    fleet_b = FleetCoordinator(env=env, graph=world.graph, fleet=[second], warehouses=[], charging_stations=[])
    c1 = world.node("C1")  # prepares the layout, which binds the nodes

    bindings = [entity for entity in env.entities.live() if isinstance(entity, NodeBinding)]
    assert sorted(binding.id for binding in bindings) == ["C1", "C2", "IN", "OUT"]
    assert repr(bindings[0]) == f"NodeBinding(id={bindings[0].id!r})"
    equal = Node(id="C1", x=5.0, y=0.0)  # equal to the canonical node, another object
    assert env.entities.node_binding(equal) is env.entities.bind_node(c1)
    with pytest.raises(LayoutError, match="exact canonical"):  # a layout binds only its canonical nodes
        env.entities.bind_node(equal)
    assert env.entities.bind_node(c1).agvs == [first.id, second.id]
    assert (first.fleet, second.fleet) == (fleet_a, fleet_b)


def test_same_id_different_node_raises() -> None:
    """The layout binds every node of its network at preparation, and only those: binding a canonical node again
    returns its binding, a node equal to it finds the binding, a different node with a bound id is refused, and a
    network node with a reserved id fails the preparation."""
    env = Environment()
    world = declare(env, _line_graph()[1])
    out = world.node("OUT")  # prepares the layout
    bound = env.entities.node_binding(out)
    assert bound is not None and env.entities.bind_node(out) is bound  # bound at preparation: the existing binding
    assert env.entities.node_binding(Node(id="OUT", x=0.0, y=0.0)) is bound  # an equal node
    with pytest.raises(LayoutError, match="exact canonical"):
        env.entities.bind_node(Node(id="OUT", x=1.0, y=0.0))
    assert env.entities.node_binding(Node(id="OUT", x=1.0, y=0.0)) is None
    assert env.entities.node_binding(Node(id="B", x=0.0, y=0.0)) is None
    reserved = Environment()
    a, b = Node(id="node-3", x=0.0, y=0.0), Node(id="B", x=1.0, y=0.0)
    declare(reserved, LayoutGraph([a, b], [Arc(a, b)]))
    with pytest.raises(LayoutError, match="reserved"):
        reserved.layout.prepare()
    assert not any(entity.kind == "node" for entity in reserved.entities.live())


def test_agv_moves_update_node_agvs() -> None:
    env = Environment()
    world = declare(env, _line_graph()[1])
    agv = AGV(env=env, agv_type=_agv_type())  # no starting node
    loose = Node(id="LOOSE", x=9.0, y=9.0)  # not a node of the network
    FleetCoordinator(env=env, graph=world.graph, fleet=[agv], warehouses=[], charging_stations=[])
    nodes = [world.node(name) for name in ("OUT", "C1", "C2", "IN")]  # prepares the layout
    binding = env.entities.bind_node

    agv.current_node = nodes[0]
    agv.current_node = nodes[0]
    assert binding(nodes[0]).agvs == [agv.id]
    with pytest.raises(LayoutError, match="exact canonical"):  # the layout refuses a node outside its network
        agv.current_node = loose
    assert binding(nodes[0]).agvs == [agv.id] and agv.current_node is nodes[0]
    agv.current_node = nodes[3]
    assert binding(nodes[0]).agvs == []
    assert binding(nodes[3]).agvs == [agv.id]
    agv.current_node = None
    assert all(not binding(node).agvs for node in nodes)


def test_node_state_tracks_positions_and_reservations_during_a_run() -> None:
    env = Environment()
    coordinator, agvs, wh_a, wh_b, world = _system(env, resource_traffic=True)  # one AGV: a line has no passing
    orders = [coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b) for _ in range(3)]
    for order in orders:
        coordinator.submit(order)
    traffic = coordinator.traffic_controller
    assert isinstance(traffic, ReservationController)

    env.activate()
    assert env.initial_state["C1"]["reserved_by"] == ("agv-0",)
    nodes = [world.node(name) for name in ("OUT", "C1", "C2", "IN")]

    def check(*, quiescent: bool) -> None:
        for node in nodes:
            fields = env.entities.bind_node(node).snapshot()
            assert fields["agvs"] == [agv.id for agv in agvs if agv.current_node == node]
            held = {agv.id for agv in agvs if node in traffic.ledger.held(agv)}
            assert set(fields["reserved_by"]) == held
            if quiescent:
                assert held == set(fields["agvs"])

    moved = 0
    while env.peek() < float("inf"):
        env.step()
        check(quiescent=False)
        moved += 1
    check(quiescent=True)
    assert moved > 10
    assert all(order.status is OrderStatus.COMPLETED for order in orders)


def test_placement_bookkeeping_changes_no_entity_state() -> None:
    env = Environment()
    _system(env, n_agvs=2, resource_traffic=True)
    env.activate()
    assert env.peek() == math.inf  # placement schedules nothing
    assert env.initial_state["C1"]["reserved_by"] == ("agv-0",)
    before = env.entities.snapshot()
    env.run(until=1)
    assert env.entities.snapshot() == before == dict(env.initial_state)


# --- ordering ----------------------------------------------------------------------------------------------

_HASH_SEED_SCRIPT = """
from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.reservation import ReservationController
from simulatte.layout import Explicit

names = ["n%02d" % i for i in (7, 3, 11, 0, 5, 9, 1, 12, 4, 8, 2, 10, 6)]
nodes = [Node(id=name, x=float(i), y=0.0) for i, name in enumerate(names)]
env = Environment(seed=1)
env.layout.set_network(Explicit(LayoutGraph(nodes, [Arc(source=a, target=b) for a, b in zip(nodes, nodes[1:])])))
agv_type = AGVType(name="t", speed_profile=TrapezoidalProfile(max_speed=1.0, acceleration=1.0, deceleration=1.0),
                   battery_capacity=1.0, weight_capacity=1.0, volume_capacity=1.0, length=0.5)
first, second = AGV(env=env, agv_type=agv_type), AGV(env=env, agv_type=agv_type)
traffic = ReservationController(graph=env.layout.graph(), env=env)
FleetCoordinator(env=env, graph=env.layout.graph(), fleet=[first, second], warehouses=[], charging_stations=[],
                 traffic_controller=traffic)
env.activate()
graph = env.layout.prepare().network.graph
claim = traffic.next_claim(second, list(reversed(graph.nodes)), 0)
print([n.id for n in graph.nodes])
print([item.resource.id for item in claim.items])
print([entity.id for entity in env.entities.live()])
"""


def test_graph_nodes_order_stable_across_hash_seeds() -> None:
    outputs = set()
    for hash_seed in ("0", "1", "123", "4242"):
        completed = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_SCRIPT],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).parent,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.add(completed.stdout)
    assert len(outputs) == 1
    graph_nodes, claim, live = outputs.pop().splitlines()
    names = ["n07", "n03", "n11", "n00", "n05", "n09", "n01", "n12", "n04", "n08", "n02", "n10", "n06"]
    assert graph_nodes == repr(names)  # insertion order
    assert claim == repr(["n06~n10", "n10"])  # the first leg of the reversed line: its segment, then its end node
    segments = sorted("~".join(sorted(pair)) for pair in zip(names, names[1:]))
    # Nodes bound sorted by id, then the segment entities attached at activation in id order.
    assert live == repr(["agv-0", "agv-1", "fleet-0", *sorted(names), *segments])


# --- order retirement --------------------------------------------------------------------------------------


def test_order_retired_on_terminal_status_only() -> None:
    env = Environment()
    coordinator, (agv,), wh_a, wh_b, _ = _system(env)
    order = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    at_retirement: list[tuple[str, bool, bool, Any]] = []
    hooks_ran: list[bool] = []

    def on_retired(event: EntityRetired) -> None:
        at_retirement.append(
            (
                event.entity,
                order.id in coordinator._active_missions,
                agv in coordinator._agv_mission,
                agv.order,
            )
        )

    env.bus.subscribe(on_retired, (EntityRetired,))
    coordinator.on_delivery_complete(lambda o, a: hooks_ran.append(env.entities.is_live(o)))
    coordinator.submit(order)

    env.run(until=1.0)
    assert order.status is OrderStatus.DISPATCHED and agv.order is order
    coordinator._active_missions[order.id].interrupt("breakdown")  # recoverable: re-queued before pickup
    env.run(until=1.5)
    assert env.entities.is_live(order)
    assert order.status is OrderStatus.DISPATCHED  # re-dispatched from the queue at once
    assert at_retirement == []

    env.run()
    assert order.status is OrderStatus.COMPLETED
    assert hooks_ran == [True]  # delivery hooks ran while the order was live
    assert at_retirement == [(order.id, False, False, None)]  # after the mission bookkeeping cleanup
    assert not env.entities.is_live(order)
    assert env.entities.get(order.id) is order  # still referenced here


def test_cancelled_and_failed_orders_retire() -> None:
    env = Environment()
    coordinator, (agv,), wh_a, wh_b, _ = _system(env)
    coordinator._max_dispatch_retries = 2
    coordinator._pending_retry_delay = 0.5
    heavy = SKU(id="HEAVY", weight=1000.0, volume=0.1)
    wh_a.inventory[heavy] = type(wh_a.inventory[SKU_A])(env, init=5)
    env.activate()

    busy = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    queued = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    unfulfillable = coordinator.create_order(sku=heavy, quantity=1, origin=wh_a, destination=wh_b)
    never_submitted = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    for order in (busy, queued, unfulfillable):
        coordinator.submit(order)
    assert coordinator.snapshot()["pending"] == [queued.id, unfulfillable.id]

    coordinator.cancel(queued)  # pending: retired at once
    assert queued.status is OrderStatus.CANCELLED and not env.entities.is_live(queued)
    coordinator.cancel(never_submitted)  # no mission: retired at once
    assert not env.entities.is_live(never_submitted)
    coordinator.cancel(never_submitted)  # cancelling a retired order again is harmless

    env.run(until=0.6)
    assert unfulfillable.status is OrderStatus.PENDING and env.entities.is_live(unfulfillable)
    env.run(until=1.1)
    assert unfulfillable.status is OrderStatus.FAILED and not env.entities.is_live(unfulfillable)

    coordinator.cancel(busy)  # active: the mission retires it after its cleanup
    assert env.entities.is_live(busy)
    env.run()
    assert busy.status is OrderStatus.CANCELLED and not env.entities.is_live(busy)
    assert agv.order is None


@pytest.mark.parametrize("phase", ["repositioning", "returning cargo after cancel"])
def test_discarded_mission_does_not_retire_its_order(phase: str) -> None:
    """A mission generator closed unfinished (garbage collection) must not emit a retirement."""
    env = Environment()
    coordinator, (agv,), wh_a, wh_b, world = _system(env)

    class Return:
        def reposition(self, agv: AGV, context: Any) -> Node:
            return world.node("OUT")

    coordinator._repositioning_policy = Return()
    order = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(order)
    env.activate()
    if phase == "repositioning":
        while order.status is not OrderStatus.COMPLETED:
            env.step()
        expected = OrderStatus.COMPLETED
    else:
        while order.status is not OrderStatus.IN_TRANSIT:
            env.step()
        env.run(until=env.now + 3.0)
        coordinator.cancel(order)  # the interrupt handler drives the cargo back to the origin
        expected = OrderStatus.CANCELLED
    env.run(until=env.now + 0.5)
    assert agv.current_load is not None or phase == "repositioning"
    process = coordinator._active_missions[order.id]
    process._generator.close()
    assert order.status is expected and env.entities.is_live(order)
    assert order.id not in coordinator._active_missions


def test_agv_order_kept_when_an_idle_hook_dispatches_the_next_mission() -> None:
    env = Environment()
    coordinator, (agv,), wh_a, wh_b, _ = _system(env)
    first = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    second = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    coordinator.on_agv_idle(lambda a: coordinator.submit(second) if second.status is OrderStatus.PENDING else None)
    at_first_retirement: list[Any] = []
    env.bus.subscribe(lambda event: at_first_retirement.append(agv.order), (EntityRetired,))
    coordinator.submit(first)
    env.run()
    # Cleanup and retirement finish before idle hooks can dispatch a new mission.
    assert at_first_retirement == [None, None]
    assert first.status is second.status is OrderStatus.COMPLETED


def test_direct_order_attached_at_submit() -> None:
    env = Environment()
    coordinator, _, wh_a, wh_b, _ = _system(env)
    order = TransferOrder(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b, created_at=0.0)
    assert order.id is None
    coordinator.submit(order)
    assert (order.id, order.fleet_id) == ("order-0", coordinator.id)
    assert env.entities.is_live(order)


def test_replenishment_orders_are_attached_and_retired() -> None:
    env = Environment()
    coordinator, _, wh_a, wh_b, _ = _system(env)
    coordinator.add_replenishment_policy(ReorderPointPolicy({SKU_A: 1}, {SKU_A: 2}), wh_b, check_interval=5.0)
    created: list[EntityCreated] = []
    env.bus.subscribe(created.append, (EntityCreated,))
    env.run(until=60)

    orders = [env.entities.get(event.entity) for event in created if event.kind == "order"]
    assert orders and wh_b.get_inventory_level(SKU_A) >= 1
    for order in orders:
        assert isinstance(order, TransferOrder) and order.fleet_id == coordinator.id
        assert env.entities.is_live(order) is (order.status is not OrderStatus.COMPLETED)


# --- replay ------------------------------------------------------------------------------------------------


def test_replay_equals_live_for_creation_and_retirement() -> None:
    env = Environment(seed=3, debug=True)
    replay = ReplayChecker(env)  # subscribed before any entity exists: replay starts from {}
    coordinator, agvs, wh_a, wh_b, _ = build_simple_system(env, n_agvs=2, layout=env.layout)
    orders = [coordinator.create_order(sku=sku, quantity=2, origin=wh_a, destination=wh_b) for sku in wh_a.inventory]
    for order in orders:
        coordinator.submit(order)
    late = coordinator.create_order(sku=orders[0].sku, quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(late)
    coordinator.cancel(late)
    env.run(until=5)
    after_activation = coordinator.create_order(sku=orders[0].sku, quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(after_activation)
    env.run()

    assert replay.kinds() == {entity.id: entity.kind for entity in env.entities.live()}
    assert all(order.status is OrderStatus.COMPLETED for order in [*orders, after_activation])
    assert late.status is OrderStatus.CANCELLED
    assert replay.checked.count("entity.retired") == len(orders) + 2
    assert replay.checked.count("entity.created") == len(env.entities.live()) + len(orders) + 2
    assert not [entity for entity in env.entities.live() if isinstance(entity, TransferOrder)]
