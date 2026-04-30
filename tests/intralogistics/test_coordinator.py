from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.battery import Battery
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.warehouse import Warehouse


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env() -> Environment:
    return Environment()


@pytest.fixture
def sku_a() -> SKU:
    return SKU(id="SKU-A", weight=5.0, volume=0.1)


@pytest.fixture
def simple_speed() -> TrapezoidalProfile:
    """Constant-speed profile: max_speed=10, instant accel/decel."""
    return TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)


def _make_agv_type(speed: TrapezoidalProfile) -> AGVType:
    return AGVType(
        name="test-type",
        speed_profile=speed,
        battery_capacity=1000.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        load_time_fn=lambda: 1.0,
        unload_time_fn=lambda: 1.0,
        low_battery_threshold=0.2,
        critical_battery_threshold=0.05,
    )


def _build_simple_system(
    env: Environment,
    sku: SKU,
    speed: TrapezoidalProfile,
    *,
    battery_capacity: float = 1000.0,
    initial_battery: float | None = None,
    origin_inventory: int = 100,
) -> tuple[FleetCoordinator, AGV, Warehouse, Warehouse]:
    """Build a minimal 3-node linear graph: WH_A(out) -- corridor -- WH_B(in).

    WH_A has output_bay=node_a_out, WH_B has input_bay=node_b_in.
    """
    node_a_out = Node(id="WH_A_OUT", x=0.0, y=0.0)
    node_corridor = Node(id="CORRIDOR", x=5.0, y=0.0)
    node_b_in = Node(id="WH_B_IN", x=10.0, y=0.0)

    arcs = [
        Arc(source=node_a_out, target=node_corridor),
        Arc(source=node_corridor, target=node_b_in),
    ]
    graph = LayoutGraph([node_a_out, node_corridor, node_b_in], arcs)

    wh_a = Warehouse(
        env=env,
        name="WH-A",
        input_bays=[node_a_out],
        output_bays=[node_a_out],
        n_slots=2,
        products=[sku],
        initial_inventory={sku: origin_inventory},
        pick_time_fn=lambda s, q: 1.0,
        put_time_fn=lambda s, q: 1.0,
    )

    wh_b = Warehouse(
        env=env,
        name="WH-B",
        input_bays=[node_b_in],
        output_bays=[node_b_in],
        n_slots=2,
        products=[sku],
        initial_inventory={sku: 0},
        pick_time_fn=lambda s, q: 1.0,
        put_time_fn=lambda s, q: 1.0,
    )

    agv_type = AGVType(
        name="test-type",
        speed_profile=speed,
        battery_capacity=battery_capacity,
        weight_capacity=100.0,
        volume_capacity=10.0,
        load_time_fn=lambda: 1.0,
        unload_time_fn=lambda: 1.0,
        low_battery_threshold=0.2,
        critical_battery_threshold=0.05,
    )
    agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a_out)
    if initial_battery is not None:
        agv.battery.level = initial_battery

    coordinator = FleetCoordinator(
        env=env,
        graph=graph,
        fleet=[agv],
        warehouses=[wh_a, wh_b],
        charging_stations=[],
    )

    return coordinator, agv, wh_a, wh_b


# ===========================================================================
# Sub-commit 1: Basic lifecycle tests
# ===========================================================================


class TestBasicLifecycle:
    """Order goes through PENDING -> DISPATCHED -> PICKING -> IN_TRANSIT -> DELIVERING -> COMPLETED."""

    def test_full_order_lifecycle(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_a, destination=wh_b
        )
        assert order.status == OrderStatus.PENDING
        assert order.created_at == 0.0

        coordinator.submit(order)
        env.run()

        # Order completed
        assert order.status == OrderStatus.COMPLETED
        # Timestamps set
        assert order.dispatched_at is not None
        assert order.picked_at is not None
        assert order.delivered_at is not None
        assert order.dispatched_at <= order.picked_at <= order.delivered_at

    def test_agv_returns_to_idle(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert agv.state == AGVState.IDLE

    def test_inventory_changes(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(
            env, sku_a, simple_speed, origin_inventory=100
        )
        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert wh_a.get_inventory_level(sku_a) == 90
        assert wh_b.get_inventory_level(sku_a) == 10

    def test_agv_state_transitions(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Track AGV state transitions during the mission."""
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)

        states_seen: list[AGVState] = []
        original_transition = agv.transition_to

        def tracking_transition(new_state: AGVState) -> None:
            if new_state not in states_seen:
                states_seen.append(new_state)
            original_transition(new_state)

        agv.transition_to = tracking_transition  # type: ignore[assignment]

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        # Should have seen these states
        # The AGV starts at origin, so TRAVELING_EMPTY is minimal but still called
        assert AGVState.WAITING_LOAD in states_seen
        assert AGVState.TRAVELING_LOADED in states_seen
        assert AGVState.WAITING_UNLOAD in states_seen
        assert AGVState.IDLE in states_seen

    def test_create_order_sets_created_at(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)

        def delayed_create():
            yield env.timeout(5.0)
            order = coordinator.create_order(
                sku=sku_a, quantity=1, origin=wh_a, destination=wh_b
            )
            assert order.created_at == 5.0

        env.process(delayed_create())
        env.run()

    def test_agv_current_node_updated(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        # After delivery the AGV is at the destination input bay
        assert agv.current_node is not None
        assert agv.current_node.id == "WH_B_IN"

    def test_no_agv_available_queues_order(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """When the only AGV is busy, a second order should be queued."""
        coordinator, agv, wh_a, wh_b = _build_simple_system(
            env, sku_a, simple_speed, origin_inventory=200
        )
        order1 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        order2 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)

        coordinator.submit(order1)
        coordinator.submit(order2)

        # order2 should be pending
        assert order2.status == OrderStatus.PENDING
        assert order2 in coordinator._pending_queue
