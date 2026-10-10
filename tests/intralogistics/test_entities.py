"""Intralogistics entities: kinds, node bindings, deterministic ordering and order retirement (spec §5)."""

from __future__ import annotations

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
from simulatte.intralogistics.traffic import ResourceBasedTrafficManager
from simulatte.intralogistics.warehouse import Warehouse

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


def _warehouses(env: Environment, nodes: list[Node]) -> tuple[Warehouse, Warehouse]:
    def make(name: str, bay: Node, level: int) -> Warehouse:
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

    return make("WH-A", nodes[0], 100), make("WH-B", nodes[-1], 0)


def _system(
    env: Environment, *, n_agvs: int = 1, resource_traffic: bool = False
) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, list[Node]]:
    """A line OUT - C1 - C2 - IN with WH-A at OUT and WH-B at IN; AGVs start at C1, C2, ..."""
    nodes, graph = _line_graph()
    wh_a, wh_b = _warehouses(env, nodes)
    agvs = [AGV(env=env, agv_type=_agv_type(), initial_node=nodes[1 + i]) for i in range(n_agvs)]
    traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1) if resource_traffic else None
    coordinator = FleetCoordinator(
        env=env, graph=graph, fleet=agvs, warehouses=[wh_a, wh_b], charging_stations=[], traffic_manager=traffic
    )
    return coordinator, agvs, wh_a, wh_b, nodes


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
    coordinator, (agv,), wh_a, wh_b, nodes = _system(env)
    ChargingStation(env=env, name="CS", node=nodes[2], n_slots=1, supports_swap=True, swap_pool_size=2)
    ParkingArea(env=env, name="PARK", node=nodes[1], capacity=2)
    order = coordinator.create_order(sku=SKU_A, quantity=3, origin=wh_a, destination=wh_b)

    assert [(e.kind, e.entity) for e in created] == [
        ("warehouse", "WH-A"),
        ("warehouse", "WH-B"),
        ("agv", "agv-0"),
        ("fleet", "fleet-0"),
        ("node", "C1"),
        ("node", "C2"),
        ("node", "IN"),
        ("node", "OUT"),
        ("charging_station", "CS"),
        ("parking_area", "PARK"),
        ("order", "order-0"),
    ]
    assert (agv.id, agv.agv_id, coordinator.id, order.id) == ("agv-0", "agv-0", "fleet-0", "order-0")

    env.activate()
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
    assert state["fleet-0"] == {"$kind": "fleet", "pending": (), "label": "fleet-0"}
    assert state["WH-A"] == {"$kind": "warehouse", "inventory": {"SKU-A": 100.0}, "slots_in_use": 0, "label": "WH-A"}
    assert state["CS"] == {"$kind": "charging_station", "slots_in_use": 0, "swap_pool": 2.0, "label": "CS"}
    assert state["PARK"] == {"$kind": "parking_area", "parked": (), "label": "PARK"}
    assert state["C1"] == {"$kind": "node", "x": 5.0, "y": 0.0, "agvs": ("agv-0",), "reserved_by": (), "label": "C1"}

    agv.current_load = {SKU_A: 2}
    assert agv.snapshot()["load"] == {"SKU-A": 2}


def test_facility_snapshots_follow_their_resources() -> None:
    env = Environment()
    nodes, _ = _line_graph()
    station = ChargingStation(env=env, name="CS", node=nodes[0], n_slots=1, label="Charger")
    area = ParkingArea(env=env, name="PARK", node=nodes[0], capacity=2)
    agvs = [AGV(env=env, agv_type=_agv_type(), agv_id=name, initial_node=nodes[0]) for name in ("B", "A")]

    def park() -> Any:
        for agv in agvs:
            yield from area.enter(agv)

    env.process(park())
    env.run()
    assert station.snapshot() == {"slots_in_use": 0, "swap_pool": None, "label": "Charger"}
    assert area.snapshot()["parked"] == ["B", "A"]  # arrival order
    area.leave(agvs[0])
    assert area.snapshot()["parked"] == ["A"]


def test_reserved_agv_names_rejected_and_custom_names_kept() -> None:
    env = Environment()
    with pytest.raises(ValueError, match="reserved"):
        AGV(env=env, agv_type=_agv_type(), agv_id="agv-7")
    nodes, graph = _line_graph()
    agv = AGV(env=env, agv_type=_agv_type(), agv_id="forklift", label="Forklift A")
    fleet = FleetCoordinator(env=env, graph=graph, fleet=[agv], warehouses=[], charging_stations=[], name="yard")
    assert (agv.id, agv.label, fleet.id) == ("forklift", "Forklift A", "yard")


# --- node bindings -----------------------------------------------------------------------------------------


def test_shared_graph_two_fleets_one_binding_per_node() -> None:
    env = Environment()
    nodes, graph = _line_graph()
    first = AGV(env=env, agv_type=_agv_type(), initial_node=nodes[1])
    fleet_a = FleetCoordinator(env=env, graph=graph, fleet=[first], warehouses=[], charging_stations=[])
    second = AGV(env=env, agv_type=_agv_type(), initial_node=nodes[1])  # created after the binding exists
    fleet_b = FleetCoordinator(env=env, graph=graph, fleet=[second], warehouses=[], charging_stations=[])

    bindings = [entity for entity in env.entities.live() if isinstance(entity, NodeBinding)]
    assert sorted(binding.id for binding in bindings) == ["C1", "C2", "IN", "OUT"]
    assert repr(bindings[0]) == f"NodeBinding(id={bindings[0].id!r})"
    assert env.entities.bind_node(nodes[1]) is env.entities.bind_node(Node(id="C1", x=5.0, y=0.0))
    assert env.entities.bind_node(nodes[1]).agvs == [first.id, second.id]
    assert (first.fleet, second.fleet) == (fleet_a, fleet_b)


def test_same_id_different_node_raises() -> None:
    env = Environment()
    env.entities.bind_node(Node(id="A", x=0.0, y=0.0))
    with pytest.raises(ValueError, match="already bound to a different node"):
        env.entities.bind_node(Node(id="A", x=1.0, y=0.0))
    assert env.entities.node_binding(Node(id="A", x=1.0, y=0.0)) is None
    assert env.entities.node_binding(Node(id="B", x=0.0, y=0.0)) is None
    with pytest.raises(ValueError, match="reserved"):
        env.entities.bind_node(Node(id="node-3", x=0.0, y=0.0))


def test_agv_moves_update_node_agvs() -> None:
    env = Environment()
    nodes, graph = _line_graph()
    agv = AGV(env=env, agv_type=_agv_type())  # no starting node
    loose = Node(id="LOOSE", x=9.0, y=9.0)  # never bound
    FleetCoordinator(env=env, graph=graph, fleet=[agv], warehouses=[], charging_stations=[])
    binding = env.entities.bind_node

    agv.current_node = nodes[0]
    agv.current_node = nodes[0]
    assert binding(nodes[0]).agvs == [agv.id]
    agv.current_node = loose
    assert binding(nodes[0]).agvs == []
    agv.current_node = nodes[3]
    assert binding(nodes[3]).agvs == [agv.id]
    agv.current_node = None
    assert all(not binding(node).agvs for node in nodes)


def test_node_state_tracks_positions_and_reservations_during_a_run() -> None:
    env = Environment()
    coordinator, agvs, wh_a, wh_b, nodes = _system(env, resource_traffic=True)  # one AGV: a line has no passing
    orders = [coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b) for _ in range(3)]
    for order in orders:
        coordinator.submit(order)
    traffic = coordinator._traffic_manager
    assert isinstance(traffic, ResourceBasedTrafficManager)

    env.activate()
    assert env.initial_state["C1"]["reserved_by"] == ("agv-0",)

    def check(*, quiescent: bool) -> None:
        for node in nodes:
            fields = env.entities.bind_node(node).snapshot()
            assert fields["agvs"] == [agv.id for agv in agvs if agv.current_node == node]
            requests = traffic._node_requests.items()
            held = {agv.id for (agv, held_node), req in requests if held_node == node and req.triggered}
            assert set(fields["reserved_by"]) <= held
            if quiescent:
                assert set(fields["reserved_by"]) == held == set(fields["agvs"])

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
    assert env.peek() == 0  # the grant events of place_now
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
from simulatte.intralogistics.traffic import ResourceBasedTrafficManager

names = ["n%02d" % i for i in (7, 3, 11, 0, 5, 9, 1, 12, 4, 8, 2, 10, 6)]
nodes = [Node(id=name, x=float(i), y=0.0) for i, name in enumerate(names)]
graph = LayoutGraph(nodes, [Arc(source=a, target=b) for a, b in zip(nodes, nodes[1:])])
env = Environment(seed=1)
agv_type = AGVType(name="t", speed_profile=TrapezoidalProfile(max_speed=1.0, acceleration=1.0, deceleration=1.0),
                   battery_capacity=1.0, weight_capacity=1.0, volume_capacity=1.0)
first, second = AGV(env=env, agv_type=agv_type), AGV(env=env, agv_type=agv_type)
traffic = ResourceBasedTrafficManager(graph=graph, env=env)
FleetCoordinator(env=env, graph=graph, fleet=[first, second], warehouses=[], charging_stations=[],
                 traffic_manager=traffic)
traffic.register_intent(first, nodes)
result = traffic.check_path(second, list(reversed(nodes)))
print([n.id for n in graph.nodes])
print([n.id for n in result.conflict_nodes])
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
    graph_nodes, conflicts, live = outputs.pop().splitlines()
    names = ["n07", "n03", "n11", "n00", "n05", "n09", "n01", "n12", "n04", "n08", "n02", "n10", "n06"]
    assert graph_nodes == repr(names)  # insertion order
    assert conflicts == repr(list(reversed(names))[1:-1])  # path order of the checked path
    assert live == repr(["agv-0", "agv-1", "fleet-0", *sorted(names)])  # nodes bound sorted by id


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
    coordinator, (agv,), wh_a, wh_b, nodes = _system(env)

    class Return:
        def reposition(self, agv: AGV, context: Any) -> Node:
            return nodes[0]

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
    # The hook dispatched the second mission before the first one's cleanup, which keeps the AGV's order.
    assert at_first_retirement == [second, None]
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
    coordinator, agvs, wh_a, wh_b, _ = build_simple_system(env, n_agvs=2)
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
