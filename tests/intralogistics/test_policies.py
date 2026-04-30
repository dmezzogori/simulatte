from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.policies import (
    DispatchStrategy,
    LoadRecoveryStrategy,
    NearestIdleStrategy,
    NearestParkingPolicy,
    ReorderPointPolicy,
    RepositioningContext,
    RepositioningPolicy,
    ReplenishmentPolicy,
    ResumeDelivery,
    ReturnToOrigin,
    RoundRobinStrategy,
    StayInPlace,
)
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.warehouse import Warehouse


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def env() -> Environment:
    return Environment()


@pytest.fixture
def speed_profile() -> TrapezoidalProfile:
    return TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)


@pytest.fixture
def sku_a() -> SKU:
    return SKU(id="A", weight=1.0, volume=0.01)


def _make_agv(
    env: Environment,
    speed_profile: TrapezoidalProfile,
    agv_id: str,
    node: Node,
    *,
    state: AGVState = AGVState.IDLE,
    weight_capacity: float = 100.0,
    volume_capacity: float = 10.0,
) -> AGV:
    agv_type = AGVType(
        name="test",
        speed_profile=speed_profile,
        battery_capacity=100.0,
        weight_capacity=weight_capacity,
        volume_capacity=volume_capacity,
    )
    agv = AGV(env=env, agv_type=agv_type, agv_id=agv_id, initial_node=node)
    agv.transition_to(state)
    return agv


def _make_warehouse(
    env: Environment,
    name: str,
    input_bays: list[Node],
    output_bays: list[Node],
    products: list[SKU],
    initial_inventory: dict[SKU, int] | None = None,
) -> Warehouse:
    return Warehouse(
        env=env,
        name=name,
        input_bays=input_bays,
        output_bays=output_bays,
        n_slots=5,
        products=products,
        initial_inventory=initial_inventory,
        pick_time_fn=lambda _sku, _qty: 1.0,
        put_time_fn=lambda _sku, _qty: 1.0,
    )


def _make_linear_graph() -> tuple[list[Node], LayoutGraph]:
    """Create a simple linear graph:  N0 -- N1 -- N2 -- N3  (10 units apart)."""
    nodes = [
        Node(id="N0", x=0.0, y=0.0),
        Node(id="N1", x=10.0, y=0.0),
        Node(id="N2", x=20.0, y=0.0),
        Node(id="N3", x=30.0, y=0.0),
    ]
    arcs = [
        Arc(source=nodes[0], target=nodes[1]),
        Arc(source=nodes[1], target=nodes[2]),
        Arc(source=nodes[2], target=nodes[3]),
    ]
    return nodes, LayoutGraph(nodes, arcs)


# ── DispatchStrategy: NearestIdleStrategy ─────────────────────────────


class TestNearestIdleStrategy:
    def test_selects_closest_idle_compatible_agv(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        # Warehouse origin with output bay at N0
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        # AGV close (at N1, distance 10) and AGV far (at N3, distance 30)
        agv_close = _make_agv(env, speed_profile, "agv-close", nodes[1])
        agv_far = _make_agv(env, speed_profile, "agv-far", nodes[3])

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        selected = strategy.select(order, [agv_far, agv_close], graph)
        assert selected is agv_close

    def test_returns_none_when_no_idle_agv(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        agv = _make_agv(env, speed_profile, "agv-busy", nodes[1], state=AGVState.TRAVELING_LOADED)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        assert strategy.select(order, [agv], graph) is None

    def test_tie_break_by_agv_id(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        # Both at same distance (N1)
        agv_b = _make_agv(env, speed_profile, "b-agv", nodes[1])
        agv_a = _make_agv(env, speed_profile, "a-agv", nodes[1])

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        selected = strategy.select(order, [agv_b, agv_a], graph)
        assert selected is agv_a  # "a-agv" < "b-agv" lexicographically

    def test_uses_per_agv_nearest_output_bay(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        node_a = Node(id="A", x=0.0, y=0.0)
        node_a_bay = Node(id="A_BAY", x=0.0, y=1.0)
        node_far = Node(id="FAR", x=100.0, y=0.0)
        node_far_bay = Node(id="FAR_BAY", x=100.0, y=1.0)
        node_dest = Node(id="DEST", x=50.0, y=50.0)
        graph = LayoutGraph(
            [node_a, node_a_bay, node_far, node_far_bay, node_dest],
            [
                Arc(source=node_a, target=node_a_bay),
                Arc(source=node_far, target=node_far_bay),
                Arc(source=node_a_bay, target=node_dest),
                Arc(source=node_far_bay, target=node_dest),
                Arc(source=node_a, target=node_far),
            ],
        )
        wh = _make_warehouse(env, "WH", [node_dest], [node_a_bay, node_far_bay], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [node_dest], [node_dest], [sku_a])

        agv_near_a = _make_agv(env, speed_profile, "agv-a", node_a)
        agv_at_far_bay = _make_agv(env, speed_profile, "agv-b", node_far_bay)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        selected = strategy.select(order, [agv_near_a, agv_at_far_bay], graph)
        assert selected is agv_at_far_bay

    def test_returns_none_when_no_compatible_agv(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        # AGV with too-small capacity
        agv = _make_agv(env, speed_profile, "agv-weak", nodes[1], weight_capacity=0.1)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        assert strategy.select(order, [agv], graph) is None

    def test_unreachable_agv_gets_inf_distance(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        """An idle AGV on a disconnected node gets inf distance.
        If it's the only candidate, it is still selected."""
        # Disconnected graph: N0 alone, N1 alone
        n0 = Node(id="N0", x=0.0, y=0.0)
        n1 = Node(id="N1", x=10.0, y=0.0)
        graph = LayoutGraph([n0, n1], [])

        wh = _make_warehouse(env, "WH", [n0], [n0], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [n1], [n1], [sku_a])

        # AGV at n1 which is disconnected from n0 (output bay)
        agv = _make_agv(env, speed_profile, "agv-disconnected", n1)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        selected = strategy.select(order, [agv], graph)
        # Only one candidate, so it is selected despite inf distance
        assert selected is agv


# ── DispatchStrategy: RoundRobinStrategy ──────────────────────────────


class TestRoundRobinStrategy:
    def test_cycles_through_agvs(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        agv1 = _make_agv(env, speed_profile, "agv-1", nodes[0])
        agv2 = _make_agv(env, speed_profile, "agv-2", nodes[1])
        agv3 = _make_agv(env, speed_profile, "agv-3", nodes[2])

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)
        fleet = [agv1, agv2, agv3]

        strategy = RoundRobinStrategy()
        first = strategy.select(order, fleet, graph)
        second = strategy.select(order, fleet, graph)
        third = strategy.select(order, fleet, graph)
        fourth = strategy.select(order, fleet, graph)

        assert first is agv1
        assert second is agv2
        assert third is agv3
        assert fourth is agv1  # wraps around

    def test_skips_non_idle_and_incompatible(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        agv_busy = _make_agv(env, speed_profile, "agv-busy", nodes[0], state=AGVState.TRAVELING_LOADED)
        agv_weak = _make_agv(env, speed_profile, "agv-weak", nodes[1], weight_capacity=0.1)
        agv_ok = _make_agv(env, speed_profile, "agv-ok", nodes[2])

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = RoundRobinStrategy()
        selected = strategy.select(order, [agv_busy, agv_weak, agv_ok], graph)
        assert selected is agv_ok

    def test_returns_none_when_all_filtered(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        agv_busy = _make_agv(env, speed_profile, "agv-busy", nodes[0], state=AGVState.CHARGING)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = RoundRobinStrategy()
        assert strategy.select(order, [agv_busy], graph) is None


# ── RepositioningPolicy: StayInPlace ─────────────────────────────────


class TestStayInPlace:
    def test_returns_none(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "agv-1", nodes[0])
        context = RepositioningContext(
            graph=graph,
            parking_areas=[],
            charging_stations=[],
            fleet=[agv],
        )
        policy = StayInPlace()
        assert policy.reposition(agv, context) is None


# ── RepositioningPolicy: NearestParkingPolicy ────────────────────────


class TestNearestParkingPolicy:
    def test_returns_nearest_parking_node_with_capacity(
        self, env: Environment, speed_profile: TrapezoidalProfile
    ) -> None:
        nodes, graph = _make_linear_graph()
        # AGV at N0, parking at N1 (10 units) and N3 (30 units)
        agv = _make_agv(env, speed_profile, "agv-1", nodes[0])
        park_near = ParkingArea(env=env, name="P1", node=nodes[1], capacity=2)
        park_far = ParkingArea(env=env, name="P2", node=nodes[3], capacity=2)

        context = RepositioningContext(
            graph=graph,
            parking_areas=[park_far, park_near],
            charging_stations=[],
            fleet=[agv],
        )

        policy = NearestParkingPolicy()
        assert policy.reposition(agv, context) == nodes[1]

    def test_returns_none_when_all_full(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "agv-1", nodes[0])
        park = ParkingArea(env=env, name="P1", node=nodes[1], capacity=1)

        # Fill the parking area by requesting the resource (auto-triggered
        # because capacity is available)
        park._resource.request()

        context = RepositioningContext(
            graph=graph,
            parking_areas=[park],
            charging_stations=[],
            fleet=[agv],
        )

        policy = NearestParkingPolicy()
        assert policy.reposition(agv, context) is None

    def test_returns_none_when_no_parking_areas(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "agv-1", nodes[0])

        context = RepositioningContext(
            graph=graph,
            parking_areas=[],
            charging_stations=[],
            fleet=[agv],
        )

        policy = NearestParkingPolicy()
        assert policy.reposition(agv, context) is None

    def test_returns_none_when_current_node_is_none(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        """AGV with current_node=None -> return None immediately."""
        nodes, graph = _make_linear_graph()
        park = ParkingArea(env=env, name="P1", node=nodes[1], capacity=2)
        agv = _make_agv(env, speed_profile, "agv-no-node", nodes[0])
        agv.current_node = None  # Force None

        context = RepositioningContext(
            graph=graph,
            parking_areas=[park],
            charging_stations=[],
            fleet=[agv],
        )

        policy = NearestParkingPolicy()
        assert policy.reposition(agv, context) is None

    def test_parking_distance_calculation(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        """Test that distance is computed along graph path and nearest is chosen."""
        nodes, graph = _make_linear_graph()
        # AGV at N2 (20,0); parking at N1 (10,0) distance=10, N3 (30,0) distance=10
        agv = _make_agv(env, speed_profile, "agv-1", nodes[2])
        park_1 = ParkingArea(env=env, name="P1", node=nodes[1], capacity=2)
        park_3 = ParkingArea(env=env, name="P3", node=nodes[3], capacity=2)

        context = RepositioningContext(
            graph=graph,
            parking_areas=[park_1, park_3],
            charging_stations=[],
            fleet=[agv],
        )

        policy = NearestParkingPolicy()
        result = policy.reposition(agv, context)
        # Both are equidistant; min picks the first with equal key
        assert result in (nodes[1], nodes[3])

    def test_parking_unreachable_gets_inf_distance(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        """Parking area on disconnected node -> inf distance, still picked if only option."""
        n0 = Node(id="N0", x=0.0, y=0.0)
        n_island = Node(id="ISLAND", x=100.0, y=100.0)
        graph = LayoutGraph([n0, n_island], [])  # No arcs

        agv = _make_agv(env, speed_profile, "agv-1", n0)
        park = ParkingArea(env=env, name="P-ISLAND", node=n_island, capacity=2)

        context = RepositioningContext(
            graph=graph,
            parking_areas=[park],
            charging_stations=[],
            fleet=[agv],
        )

        policy = NearestParkingPolicy()
        # Only option is unreachable, but min still returns it
        result = policy.reposition(agv, context)
        assert result == n_island


# ── ReplenishmentPolicy: ReorderPointPolicy ──────────────────────────


class TestReorderPointPolicy:
    def test_triggers_order_below_threshold(self, env: Environment, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        # Monitored warehouse: low stock (5), threshold 10
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 5})
        # Source warehouse: high stock (100)
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 50},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[])

        assert len(orders) == 1
        order = orders[0]
        assert order.sku is sku_a
        assert order.quantity == 50
        assert order.origin is wh_high
        assert order.destination is wh_low
        assert order.status == OrderStatus.PENDING

    def test_no_duplicate_when_in_transit_exists(self, env: Environment, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 5})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        # Existing in-transit order for same sku to same destination
        existing = TransferOrder(
            sku=sku_a,
            quantity=50,
            origin=wh_high,
            destination=wh_low,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
        )

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 50},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[existing])
        assert len(orders) == 0

    def test_picks_source_with_highest_stock(self, env: Environment, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 5})
        wh_medium = _make_warehouse(env, "WH_MED", [nodes[1]], [nodes[1]], [sku_a], {sku_a: 50})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 200})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 30},
        )
        orders = policy.check(wh_low, [wh_low, wh_medium, wh_high], in_transit_orders=[])

        assert len(orders) == 1
        assert orders[0].origin is wh_high

    def test_sums_in_transit_quantities(self, env: Environment, sku_a: SKU) -> None:
        """S5: Policy sums in-transit quantities. With 45 on hand and 10
        in-transit (effective=55), threshold=50 should NOT trigger a new order."""
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 45})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        # In-transit order with 10 units heading to wh_low
        in_transit = TransferOrder(
            sku=sku_a,
            quantity=10,
            origin=wh_high,
            destination=wh_low,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
        )

        policy = ReorderPointPolicy(
            thresholds={sku_a: 50},
            reorder_quantity={sku_a: 30},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[in_transit])

        # effective_stock = 45 + 10 = 55 >= 50 → no new order
        assert len(orders) == 0

    def test_triggers_order_when_in_transit_insufficient(self, env: Environment, sku_a: SKU) -> None:
        """S5: When in-transit quantities are not enough, a new order is still created."""
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 30})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        # In-transit order with 10 units — effective = 30 + 10 = 40 < 50
        in_transit = TransferOrder(
            sku=sku_a,
            quantity=10,
            origin=wh_high,
            destination=wh_low,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
        )

        policy = ReorderPointPolicy(
            thresholds={sku_a: 50},
            reorder_quantity={sku_a: 30},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[in_transit])

        # effective_stock = 30 + 10 = 40 < 50 → new order
        assert len(orders) == 1
        assert orders[0].quantity == 30

    def test_excludes_completed_in_transit_from_sum(self, env: Environment, sku_a: SKU) -> None:
        """S5: Completed/failed/cancelled in-transit orders are not counted."""
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 45})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        # Completed in-transit order — should NOT be counted
        completed_order = TransferOrder(
            sku=sku_a,
            quantity=10,
            origin=wh_high,
            destination=wh_low,
            created_at=0.0,
            status=OrderStatus.COMPLETED,
        )

        policy = ReorderPointPolicy(
            thresholds={sku_a: 50},
            reorder_quantity={sku_a: 30},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[completed_order])

        # effective_stock = 45 + 0 = 45 < 50 → new order
        assert len(orders) == 1

    def test_no_other_warehouses_skips(self, env: Environment, sku_a: SKU) -> None:
        """When the only warehouse is the monitored one, no source exists -> skip."""
        nodes, _ = _make_linear_graph()
        wh_only = _make_warehouse(env, "WH_ONLY", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 5})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 50},
        )
        orders = policy.check(wh_only, [wh_only], in_transit_orders=[])

        # No other warehouses to source from -> no orders
        assert len(orders) == 0

    def test_no_order_when_above_threshold(self, env: Environment, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 100})
        wh_source = _make_warehouse(env, "WH_SRC", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 200})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 50},
        )
        orders = policy.check(wh, [wh, wh_source], in_transit_orders=[])
        assert len(orders) == 0


# ── LoadRecoveryStrategy: ReturnToOrigin ─────────────────────────────


class TestReturnToOrigin:
    def test_with_empty_load_skips_put(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        """When agv.current_load is falsy (None), the put loop is skipped."""
        nodes, _ = _make_linear_graph()
        wh_orig = _make_warehouse(env, "WH_O", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_D", [nodes[3]], [nodes[3]], [sku_a])
        agv = _make_agv(env, speed_profile, "agv-1", nodes[1])
        agv.current_load = None

        order = TransferOrder(
            sku=sku_a,
            quantity=1,
            origin=wh_orig,
            destination=wh_dest,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
            assigned_agv=agv,
        )

        strategy = ReturnToOrigin()
        gen = strategy.recover(order, agv, None)  # type: ignore[arg-type]
        list(gen)

        assert order.status == OrderStatus.PENDING
        assert order.assigned_agv is None

    def test_sets_pending_and_clears_agv(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh_orig = _make_warehouse(env, "WH_O", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_D", [nodes[3]], [nodes[3]], [sku_a])
        agv = _make_agv(env, speed_profile, "agv-1", nodes[1])

        order = TransferOrder(
            sku=sku_a,
            quantity=1,
            origin=wh_orig,
            destination=wh_dest,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
            assigned_agv=agv,
        )

        strategy = ReturnToOrigin()
        gen = strategy.recover(order, agv, None)  # type: ignore[arg-type]
        # Exhaust the generator
        list(gen)

        assert order.status == OrderStatus.PENDING
        assert order.assigned_agv is None

    def test_with_cargo_sets_pending(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        """ReturnToOrigin sets status to PENDING (physical return is coordinator's job)."""
        nodes, _ = _make_linear_graph()
        wh_orig = _make_warehouse(env, "WH_O", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 100})
        wh_dest = _make_warehouse(env, "WH_D", [nodes[3]], [nodes[3]], [sku_a])
        agv = _make_agv(env, speed_profile, "agv-1", nodes[1])
        agv.current_load = {sku_a: 10}

        order = TransferOrder(
            sku=sku_a,
            quantity=10,
            origin=wh_orig,
            destination=wh_dest,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
            assigned_agv=agv,
        )

        strategy = ReturnToOrigin()
        gen = strategy.recover(order, agv, None)  # type: ignore[arg-type]
        list(gen)

        assert order.status == OrderStatus.PENDING
        assert order.assigned_agv is None
        # Cargo is NOT returned by the strategy — coordinator handles it
        assert agv.current_load == {sku_a: 10}


# ── LoadRecoveryStrategy: ResumeDelivery ─────────────────────────────


class TestResumeDelivery:
    def test_keeps_in_transit_status(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh_orig = _make_warehouse(env, "WH_O", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_D", [nodes[3]], [nodes[3]], [sku_a])
        agv = _make_agv(env, speed_profile, "agv-1", nodes[1])

        order = TransferOrder(
            sku=sku_a,
            quantity=1,
            origin=wh_orig,
            destination=wh_dest,
            created_at=0.0,
            status=OrderStatus.DISPATCHED,
            assigned_agv=agv,
        )

        strategy = ResumeDelivery()
        gen = strategy.recover(order, agv, None)  # type: ignore[arg-type]
        list(gen)

        assert order.status == OrderStatus.IN_TRANSIT
        assert order.assigned_agv is agv


# ── Protocol conformance ─────────────────────────────────────────────


class TestProtocolConformance:
    def test_nearest_idle_is_dispatch_strategy(self) -> None:
        assert isinstance(NearestIdleStrategy(), DispatchStrategy)

    def test_round_robin_is_dispatch_strategy(self) -> None:
        assert isinstance(RoundRobinStrategy(), DispatchStrategy)

    def test_stay_in_place_is_repositioning_policy(self) -> None:
        assert isinstance(StayInPlace(), RepositioningPolicy)

    def test_nearest_parking_is_repositioning_policy(self) -> None:
        assert isinstance(NearestParkingPolicy(), RepositioningPolicy)

    def test_reorder_point_is_replenishment_policy(self) -> None:
        policy = ReorderPointPolicy(thresholds={}, reorder_quantity={})
        assert isinstance(policy, ReplenishmentPolicy)

    def test_return_to_origin_is_load_recovery_strategy(self) -> None:
        assert isinstance(ReturnToOrigin(), LoadRecoveryStrategy)

    def test_resume_delivery_is_load_recovery_strategy(self) -> None:
        assert isinstance(ResumeDelivery(), LoadRecoveryStrategy)
