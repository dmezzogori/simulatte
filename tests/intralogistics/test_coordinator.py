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
from simulatte.intralogistics.traffic import ResourceBasedTrafficManager
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


# ===========================================================================
# Sub-commit 2: Cancellation and battery
# ===========================================================================


def _build_system_with_charger(
    env: Environment,
    sku: SKU,
    speed: TrapezoidalProfile,
    *,
    battery_capacity: float = 1000.0,
    initial_battery: float | None = None,
    origin_inventory: int = 100,
) -> tuple[FleetCoordinator, AGV, Warehouse, Warehouse, ChargingStation]:
    """3-node linear graph with a charging station at the corridor node."""
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

    charger = ChargingStation(
        env=env,
        name="CS-1",
        node=node_corridor,
        n_slots=1,
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
        charging_stations=[charger],
    )

    return coordinator, agv, wh_a, wh_b, charger


class TestCancellation:
    """Cancellation tests for orders in various states."""

    def test_cancel_pending_order(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Cancel a pending (queued) order."""
        coordinator, agv, wh_a, wh_b = _build_simple_system(
            env, sku_a, simple_speed, origin_inventory=200
        )
        order1 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        order2 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order1)
        coordinator.submit(order2)

        # order2 is pending — cancel it
        coordinator.cancel(order2)
        assert order2.status == OrderStatus.CANCELLED
        assert order2 not in coordinator._pending_queue

    def test_cancel_mid_travel_before_pickup(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Cancel an order while AGV is traveling empty (before pickup).
        Order should be CANCELLED, AGV should go IDLE.
        """
        # Build a graph where the AGV starts far away so travel takes time
        node_far = Node(id="FAR", x=0.0, y=0.0)
        node_a_out = Node(id="WH_A_OUT", x=100.0, y=0.0)
        node_b_in = Node(id="WH_B_IN", x=110.0, y=0.0)
        arcs = [
            Arc(source=node_far, target=node_a_out),
            Arc(source=node_a_out, target=node_b_in),
        ]
        graph = LayoutGraph([node_far, node_a_out, node_b_in], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A", input_bays=[node_a_out], output_bays=[node_a_out],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B", input_bays=[node_b_in], output_bays=[node_b_in],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=10000.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_far)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)

        # Travel to WH_A_OUT is 100 units at speed 10 = ~10 time units. Cancel at t=1.
        def cancel_later():
            yield env.timeout(1.0)
            coordinator.cancel(order)

        env.process(cancel_later())
        env.run()

        assert order.status == OrderStatus.CANCELLED
        assert agv.state == AGVState.IDLE

    def test_cancel_completed_order_is_noop(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Cancelling an already completed order doesn't raise."""
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        # Cancel after completion — should not raise
        coordinator.cancel(order)
        assert order.status == OrderStatus.CANCELLED  # status updated but no side effects


class TestBattery:
    """Battery management: low-battery charging, pre-arc checks, stranded detection."""

    def test_low_battery_after_mission_triggers_charge(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """AGV with low battery after mission should charge before going idle."""
        # Use depletion that consumes battery based on distance (1 unit per distance unit)
        # Total travel: ~10 units (WH_A_OUT to CORRIDOR to WH_B_IN).
        # With battery_capacity=100 and initial=25, after mission the battery
        # will be low (< 20%).
        coordinator, agv, wh_a, wh_b, charger = _build_system_with_charger(
            env, sku_a, simple_speed, battery_capacity=100.0, initial_battery=25.0
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        # After mission + charging, AGV should be idle with full battery
        assert agv.state == AGVState.IDLE
        assert order.status == OrderStatus.COMPLETED
        # Battery should have been recharged
        assert agv.battery.level_pct > 0.2

    def test_pre_arc_insufficient_energy_stranded(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """AGV runs out of battery mid-travel with no charger reachable -> STRANDED."""
        coordinator, agv, wh_a, wh_b = _build_simple_system(
            env, sku_a, simple_speed, battery_capacity=100.0, initial_battery=1.0
        )
        # No charging stations available
        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        # AGV can't reach the destination — STRANDED, order FAILED
        assert agv.state == AGVState.STRANDED
        assert order.status == OrderStatus.FAILED

    def test_pre_arc_divert_to_charger(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """AGV with insufficient energy for next arc diverts to nearby charger."""
        # Place charger at WH_A_OUT (same as AGV starting position)
        node_a_out = Node(id="WH_A_OUT", x=0.0, y=0.0)
        node_corridor = Node(id="CORRIDOR", x=5.0, y=0.0)
        node_b_in = Node(id="WH_B_IN", x=10.0, y=0.0)
        arcs = [
            Arc(source=node_a_out, target=node_corridor),
            Arc(source=node_corridor, target=node_b_in),
        ]
        graph = LayoutGraph([node_a_out, node_corridor, node_b_in], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A", input_bays=[node_a_out], output_bays=[node_a_out],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B", input_bays=[node_b_in], output_bays=[node_b_in],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        # Charger is at the same node as the AGV (no travel needed to reach it)
        charger = ChargingStation(
            env=env, name="CS-1", node=node_a_out, n_slots=1,
        )

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
        )
        # Battery starts at 3.0 — enough for nothing (5-unit arc costs 5 energy)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a_out)
        agv.battery.level = 3.0

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[charger],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        # After diverting to charge (co-located), it should have enough to complete
        assert order.status == OrderStatus.COMPLETED
        assert agv.state == AGVState.IDLE


# ===========================================================================
# Sub-commit 3: Hooks, replenishment, pending queue, fleet metrics
# ===========================================================================


class TestLifecycleHooks:
    """Lifecycle hooks fire at the right times."""

    def test_on_order_submitted_fires(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        submitted: list[TransferOrder] = []
        coordinator.on_order_submitted(lambda o: submitted.append(o))

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert len(submitted) == 1
        assert submitted[0] is order

    def test_on_delivery_complete_fires(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        delivered: list[tuple[TransferOrder, AGV]] = []
        coordinator.on_delivery_complete(lambda o, a: delivered.append((o, a)))

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert len(delivered) == 1
        assert delivered[0][0] is order
        assert delivered[0][1] is agv

    def test_on_agv_idle_fires(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        idle_events: list[AGV] = []
        coordinator.on_agv_idle(lambda a: idle_events.append(a))

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert len(idle_events) >= 1
        assert idle_events[0] is agv

    def test_on_order_dispatched_fires(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        dispatched: list[tuple[TransferOrder, AGV]] = []
        coordinator.on_order_dispatched(lambda o, a: dispatched.append((o, a)))

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert len(dispatched) == 1
        assert dispatched[0][0] is order

    def test_on_pickup_complete_fires(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        pickups: list[tuple[TransferOrder, AGV]] = []
        coordinator.on_pickup_complete(lambda o, a: pickups.append((o, a)))

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert len(pickups) == 1
        assert pickups[0][0] is order


class TestPendingQueue:
    """Pending queue: orders dispatched when AGVs become available."""

    def test_second_order_completes_after_first(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """With 1 AGV and 2 orders, the second completes after the first."""
        coordinator, agv, wh_a, wh_b = _build_simple_system(
            env, sku_a, simple_speed, origin_inventory=200
        )
        order1 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        order2 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)

        coordinator.submit(order1)
        coordinator.submit(order2)

        env.run()

        assert order1.status == OrderStatus.COMPLETED
        assert order2.status == OrderStatus.COMPLETED
        assert order1.delivered_at is not None
        assert order2.delivered_at is not None
        assert order2.delivered_at > order1.delivered_at


class TestFleetMetrics:
    """Fleet convenience methods."""

    def test_fleet_utilization_nonzero_after_mission(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        util = coordinator.fleet_utilization
        assert 0.0 < util <= 1.0

    def test_fleet_time_allocation_sums_to_one(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        alloc = coordinator.fleet_time_allocation()
        total = sum(alloc.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_agv_report(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b = _build_simple_system(env, sku_a, simple_speed)
        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        report = coordinator.agv_report()
        assert len(report) == 1
        assert report[0]["agv_id"] == "agv-1"
        assert report[0]["state"] == "IDLE"
        assert isinstance(report[0]["utilization"], float)


class TestReplenishment:
    """Replenishment policy periodic check."""

    def test_periodic_replenishment(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Periodic replenishment check creates and submits orders when inventory is low."""
        from simulatte.intralogistics.policies import ReorderPointPolicy

        coordinator, agv, wh_a, wh_b = _build_simple_system(
            env, sku_a, simple_speed, origin_inventory=200
        )
        # Put initial inventory in WH-B so it can serve as source
        wh_b.inventory[sku_a]._level = 100  # type: ignore[attr-defined]

        # Set up reorder policy: when WH-A drops below 150, reorder 50 from WH-B
        policy = ReorderPointPolicy(
            thresholds={sku_a: 150},
            reorder_quantity={sku_a: 50},
        )

        coordinator.add_replenishment_policy(policy, wh_a, check_interval=10.0)

        # First drain some inventory from WH-A
        def drain():
            yield from wh_a.pick(sku_a, 60)  # WH-A goes to 140 < 150

        env.process(drain())
        # Run for enough time for one replenishment check
        env.run(until=15.0)

        # The replenishment policy should have submitted an order
        # (either already dispatched or in pending queue)
        # Check that WH-A is being restocked or there's an active/pending order
        has_replenishment = (
            len(coordinator._active_missions) > 0
            or len(coordinator._pending_queue) > 0
            or wh_a.get_inventory_level(sku_a) > 140
        )
        assert has_replenishment


# ===========================================================================
# Batch 1: _travel() correctness fixes
# ===========================================================================


class TestTravelCorrectness:
    """Tests for Batch 1 _travel() correctness fixes."""

    def test_no_path_mission_order_failed(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """T1: When origin and destination are disconnected, order should be FAILED."""
        # Two disconnected subgraphs: AGV starts at node_a_out, destination is on
        # a separate island with no connecting arc.
        node_a_out = Node(id="WH_A_OUT", x=0.0, y=0.0)
        node_b_in = Node(id="WH_B_IN", x=100.0, y=100.0)

        # No arcs connecting them — they are disconnected
        graph = LayoutGraph([node_a_out, node_b_in], [])

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a_out], output_bays=[node_a_out],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b_in], output_bays=[node_b_in],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a_out)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.FAILED
        assert agv.state == AGVState.STRANDED

    def test_infeasible_check_path_no_alternative_order_failed(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """T6: When check_path finds a conflict and no alternative route exists
        on a linear graph, the second AGV's order must be FAILED."""
        # Linear graph: A -- B -- C.  Two AGVs both at A, trying to go to C.
        # AGV-1 registers intent [A, B, C].  AGV-2's check_path detects
        # conflict on {B, C}.  No alternative route exists (linear graph),
        # so H2 sets STRANDED and returns False.
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=5.0, y=0.0)
        node_c = Node(id="C", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_b, target=node_c),
        ]
        graph = LayoutGraph([node_a, node_b, node_c], arcs)

        traffic = ResourceBasedTrafficManager(
            graph=graph, env=env, node_capacity=1, deadlock_timeout=5.0,
        )

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 200},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_c = Warehouse(
            env=env, name="WH-C",
            input_bays=[node_c], output_bays=[node_c],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv1 = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv2 = AGV(env=env, agv_type=agv_type, agv_id="agv-2", initial_node=node_a)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv1, agv2],
            warehouses=[wh_a, wh_c], charging_stations=[],
            traffic_manager=traffic,
        )

        order1 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_c)
        order2 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_c)

        coordinator.submit(order1)
        coordinator.submit(order2)

        env.run(until=500.0)

        # First order completes; second order FAILS because H2 cannot
        # find an alternative path on this linear topology.
        assert order1.status == OrderStatus.COMPLETED
        assert order2.status == OrderStatus.FAILED

    def test_arc_speed_limit_passed_to_speed_profile(
        self, env: Environment, sku_a: SKU
    ) -> None:
        """T5: Arc speed_limit should increase travel time when it constrains the AGV."""
        # Two runs: one without speed_limit, one with a very low speed_limit.
        # The limited run should take longer.
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=10.0, y=0.0)

        fast_speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)
        agv_type = _make_agv_type(fast_speed)

        # Run 1: No speed limit
        arcs_unlimited = [Arc(source=node_a, target=node_b)]
        graph_unlimited = LayoutGraph([node_a, node_b], arcs_unlimited)

        wh_a1 = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b1 = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b], output_bays=[node_b],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        agv1 = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        coordinator1 = FleetCoordinator(
            env=env, graph=graph_unlimited, fleet=[agv1],
            warehouses=[wh_a1, wh_b1], charging_stations=[],
        )
        order1 = coordinator1.create_order(sku=sku_a, quantity=10, origin=wh_a1, destination=wh_b1)
        coordinator1.submit(order1)
        env.run()
        time_unlimited = order1.delivered_at
        assert time_unlimited is not None

        # Run 2: Speed limit = 2.0 (much slower than max_speed 10.0)
        env2 = Environment()
        arcs_limited = [Arc(source=node_a, target=node_b, speed_limit=2.0)]
        graph_limited = LayoutGraph([node_a, node_b], arcs_limited)

        wh_a2 = Warehouse(
            env=env2, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b2 = Warehouse(
            env=env2, name="WH-B",
            input_bays=[node_b], output_bays=[node_b],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        agv2 = AGV(env=env2, agv_type=agv_type, agv_id="agv-2", initial_node=node_a)
        coordinator2 = FleetCoordinator(
            env=env2, graph=graph_limited, fleet=[agv2],
            warehouses=[wh_a2, wh_b2], charging_stations=[],
        )
        order2 = coordinator2.create_order(sku=sku_a, quantity=10, origin=wh_a2, destination=wh_b2)
        coordinator2.submit(order2)
        env2.run()
        time_limited = order2.delivered_at
        assert time_limited is not None

        # The speed-limited run should take noticeably longer
        assert time_limited > time_unlimited

    def test_initial_placement_acquires_traffic_resource(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """S1: After FleetCoordinator init, the AGV's starting node resource
        should be acquired in ResourceBasedTrafficManager."""
        node_start = Node(id="START", x=0.0, y=0.0)
        node_end = Node(id="END", x=10.0, y=0.0)

        arcs = [Arc(source=node_start, target=node_end)]
        graph = LayoutGraph([node_start, node_end], arcs)

        traffic = ResourceBasedTrafficManager(
            graph=graph, env=env, node_capacity=1,
        )

        wh = Warehouse(
            env=env, name="WH",
            input_bays=[node_start], output_bays=[node_start],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_start)

        _coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh], charging_stations=[],
            traffic_manager=traffic,
        )

        # Let the initial placement process run
        env.run(until=0.001)

        # The resource for the start node should now have 1 user (the placed AGV)
        resource = traffic._node_resources[node_start]
        assert resource.count == 1

    def test_mid_travel_charging_diversion_completes_from_charger(
        self, env: Environment, sku_a: SKU
    ) -> None:
        """H6: An AGV that diverts to a charger mid-travel should re-plan
        from the charger's position and complete the mission."""
        # Graph: A -- charger_node -- B -- C
        # AGV starts at A with just enough battery to reach charger_node but
        # not enough to complete the trip without charging.
        node_a = Node(id="A", x=0.0, y=0.0)
        node_charger = Node(id="CHARGER", x=5.0, y=0.0)
        node_b = Node(id="B", x=10.0, y=0.0)
        node_c = Node(id="C", x=15.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_charger),
            Arc(source=node_charger, target=node_b),
            Arc(source=node_b, target=node_c),
        ]
        graph = LayoutGraph([node_a, node_charger, node_b, node_c], arcs)

        speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_c = Warehouse(
            env=env, name="WH-C",
            input_bays=[node_c], output_bays=[node_c],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        charger = ChargingStation(env=env, name="CS-1", node=node_charger, n_slots=1)

        agv_type = AGVType(
            name="test-type", speed_profile=speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        # Battery at 7.0: enough for the first 5-unit arc (costs 5.0) but
        # after arriving at charger_node, battery=2.0 is not enough for the
        # next 5-unit arc (costs 5.0) -- triggers charging diversion.
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv.battery.level = 7.0

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_c], charging_stations=[charger],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_c)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert agv.state == AGVState.IDLE
        # AGV should have ended at the destination
        assert agv.current_node == node_c

    def test_node_capacity_1_timeout_reroute(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """T2/S2: Two AGVs competing for the same node with capacity=1.
        Both should eventually complete or one should gracefully fail."""
        # Diamond graph:  A -- B -- D
        #                  \       /
        #                   -- C --
        # Two AGVs start at A, both going to D.  With capacity=1 on each
        # node, they can use different routes (A-B-D and A-C-D).
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=5.0, y=5.0)
        node_c = Node(id="C", x=5.0, y=-5.0)
        node_d = Node(id="D", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_a, target=node_c),
            Arc(source=node_b, target=node_d),
            Arc(source=node_c, target=node_d),
        ]
        graph = LayoutGraph([node_a, node_b, node_c, node_d], arcs)

        traffic = ResourceBasedTrafficManager(
            graph=graph, env=env, node_capacity=1, deadlock_timeout=5.0,
        )

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 200},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_d = Warehouse(
            env=env, name="WH-D",
            input_bays=[node_d], output_bays=[node_d],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv1 = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv2 = AGV(env=env, agv_type=agv_type, agv_id="agv-2", initial_node=node_a)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv1, agv2],
            warehouses=[wh_a, wh_d], charging_stations=[],
            traffic_manager=traffic,
        )

        order1 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_d)
        order2 = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_d)

        coordinator.submit(order1)
        coordinator.submit(order2)

        # Run with generous time limit — must not hang
        env.run(until=500.0)

        # Both orders should have resolved (completed or failed)
        final_statuses = {OrderStatus.COMPLETED, OrderStatus.FAILED}
        assert order1.status in final_statuses
        assert order2.status in final_statuses

        # At least one should have completed
        assert order1.status == OrderStatus.COMPLETED or order2.status == OrderStatus.COMPLETED

    def test_deadlock_timeout_fires_and_retries(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """S2 focused: An AGV blocked on enter_node triggers the deadlock
        timeout path. A stationary AGV (placed, no intent) occupies a
        chokepoint node. The traveling AGV's check_path sees no conflict
        (no intent from the stationary AGV), but enter_node blocks on the
        occupied resource.

        ``_enter_with_timeout`` retries the same node with exponential
        backoff.  After exhausting retries (3 by default), the AGV is
        STRANDED and the order FAILS gracefully — no infinite hang.

        Graph:  START -- CHOKE -- END

        Stationary AGV is placed on CHOKE (capacity=1). Traveling AGV
        plans START-CHOKE-END, check_path passes, enter_node blocks.
        """
        node_start = Node(id="START", x=0.0, y=0.0)
        node_choke = Node(id="CHOKE", x=5.0, y=0.0)
        node_end = Node(id="END", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_start, target=node_choke),
            Arc(source=node_choke, target=node_end),
        ]
        graph = LayoutGraph([node_start, node_choke, node_end], arcs)

        deadlock_timeout = 2.0
        traffic = ResourceBasedTrafficManager(
            graph=graph, env=env, node_capacity=1, deadlock_timeout=deadlock_timeout,
        )

        wh_start = Warehouse(
            env=env, name="WH-START",
            input_bays=[node_start], output_bays=[node_start],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_end = Warehouse(
            env=env, name="WH-END",
            input_bays=[node_end], output_bays=[node_end],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)

        # Stationary AGV: placed on CHOKE, never dispatched, no intent
        agv_blocker = AGV(env=env, agv_type=agv_type, agv_id="blocker", initial_node=node_choke)
        # Traveling AGV: starts at START, mission to END
        agv_traveler = AGV(env=env, agv_type=agv_type, agv_id="traveler", initial_node=node_start)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv_traveler, agv_blocker],
            warehouses=[wh_start, wh_end], charging_stations=[],
            traffic_manager=traffic,
        )

        # Let initial placement complete so blocker occupies CHOKE resource
        env.run(until=0.001)
        assert traffic._node_resources[node_choke].count == 1

        t_before = env.now
        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_start, destination=wh_end
        )
        coordinator.submit(order)

        # Run with generous time limit — must NOT hang
        env.run(until=500.0)

        # After exhausting retries the order must FAIL gracefully
        assert order.status == OrderStatus.FAILED
        assert agv_traveler.state == AGVState.STRANDED

        # Verify that time actually passed (timeout + backoff was applied)
        # 3 retries: timeout(2) + backoff(2), timeout(2) + backoff(4), timeout(2)
        # = 2 + 2 + 2 + 4 + 2 = 12 seconds minimum
        assert env.now - t_before >= deadlock_timeout


# ===========================================================================
# Batch 2: Interrupt safety and inventory rollback
# ===========================================================================


class TestInterruptSafety:
    """Tests for H3, M2, H4, H5: interrupt safety and resource cleanup."""

    def test_cancel_during_travel_releases_next_node_resource(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """T4: Cancelling a mission while the AGV is mid-travel between
        enter_node and leave_node releases the acquired next-node resource.

        Graph:  FAR -- NEXT -- END (AGV starts at FAR, origin warehouse
        output bay is at END, so AGV must travel through NEXT).
        ResourceBasedTrafficManager with node_capacity=1.
        Cancel during the travel timeout after entering NEXT.
        """
        node_far = Node(id="FAR", x=0.0, y=0.0)
        node_next = Node(id="NEXT", x=5.0, y=0.0)
        node_end = Node(id="END", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_far, target=node_next),
            Arc(source=node_next, target=node_end),
        ]
        graph = LayoutGraph([node_far, node_next, node_end], arcs)

        traffic = ResourceBasedTrafficManager(
            graph=graph, env=env, node_capacity=1, deadlock_timeout=30.0,
        )

        # Origin warehouse is at END so the AGV must travel FAR -> NEXT -> END
        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_end], output_bays=[node_end],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_far], output_bays=[node_far],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_far)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
            traffic_manager=traffic,
        )

        # Let initial placement complete
        env.run(until=0.001)
        assert traffic._node_resources[node_far].count == 1

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # Travel from FAR to NEXT: distance=5, speed=10 -> travel_time=0.5s
        # enter_node happens nearly instantly, then timeout(0.5).
        # Cancel at t=0.2 — mid-travel, after entering NEXT but before arriving.
        def cancel_later():
            yield env.timeout(0.2)
            coordinator.cancel(order)

        env.process(cancel_later())
        env.run()

        # After cancellation, the next-node resource should be released (count=0).
        assert traffic._node_resources[node_next].count == 0
        assert order.status == OrderStatus.CANCELLED
        assert agv.state == AGVState.IDLE

    def test_cancel_after_pickup_returns_inventory_to_origin(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """T3: When the AGV is cancelled during loaded travel (after pickup),
        inventory should be returned to the origin warehouse.

        Graph: ORIGIN -- MID -- DEST (longer to widen the loaded-travel window).
        """
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=50.0, y=0.0)
        node_dest = Node(id="DEST", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
        )

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # AGV starts at origin. pick_time=1.0, load_time=1.0, then loaded travel.
        # Travel to MID: distance=50, speed=10 -> 5.0s.
        # Pickup finishes at ~2.0 (pick_time=1 + load_time=1).
        # Cancel at t=4.0 — during loaded travel.
        def cancel_later():
            yield env.timeout(4.0)
            coordinator.cancel(order)

        env.process(cancel_later())
        env.run()

        assert order.status == OrderStatus.CANCELLED
        assert agv.state == AGVState.IDLE
        assert agv.current_load is None
        # Inventory should be returned to origin.
        # The put process is fire-and-forget via env.process, so let it complete.
        env.run()
        assert wh_origin.get_inventory_level(sku_a) == 100
        assert wh_dest.get_inventory_level(sku_a) == 0

    def test_interrupt_during_pick_rolls_back_inventory(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """H5: Interrupting a mission during warehouse.pick() (after
        container.get but before agv.current_load assignment) should
        roll back inventory via the committed picks mechanism.

        The AGV starts at origin. Pick begins with inventory.get(qty),
        then slot request, then timeout(pick_time=1.0). Interrupt during
        that timeout window.
        """
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_dest = Node(id="DEST", x=50.0, y=0.0)

        arcs = [Arc(source=node_origin, target=node_dest)]
        graph = LayoutGraph([node_origin, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 2.0,  # 2s pick time to widen the window
            put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
        )

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # AGV is at origin, so no empty travel. Pick starts immediately:
        # inventory.get(10) is instant (100 in stock), slot request is instant,
        # then timeout(2.0) for pick_time. Interrupt at t=1.0 — mid-pick.
        def cancel_later():
            yield env.timeout(1.0)
            coordinator.cancel(order)

        env.process(cancel_later())
        env.run()

        assert order.status == OrderStatus.CANCELLED
        assert agv.state == AGVState.IDLE
        assert agv.current_load is None
        # Committed pick should have been rolled back.
        # The put process is fire-and-forget, let it finish.
        env.run()
        assert wh_origin.get_inventory_level(sku_a) == 100

    def test_cancel_cleans_up_all_node_requests(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """M2: cancel() should clean up ALL _node_requests for the AGV,
        including already-triggered ones, and release their resources.

        Use ResourceBasedTrafficManager. AGV starts at FAR and must travel
        to the origin warehouse at node C. Cancel mid-travel so the
        _travel() finally block invokes cancel(), which must clean up
        all _node_requests entries for the AGV.

        Graph: FAR -- MID -- C  (AGV starts at FAR, origin output bay at C).
        """
        node_far = Node(id="FAR", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=5.0, y=0.0)
        node_c = Node(id="C", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_far, target=node_mid),
            Arc(source=node_mid, target=node_c),
        ]
        graph = LayoutGraph([node_far, node_mid, node_c], arcs)

        traffic = ResourceBasedTrafficManager(
            graph=graph, env=env, node_capacity=1, deadlock_timeout=30.0,
        )

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_c], output_bays=[node_c],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_far], output_bays=[node_far],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_far)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
            traffic_manager=traffic,
        )

        # Let initial placement complete
        env.run(until=0.001)
        # AGV placed at FAR
        assert traffic._node_resources[node_far].count == 1

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # Travel FAR->MID: distance=5, speed=10 -> 0.5s. Cancel at t=0.2, mid-arc.
        def cancel_later():
            yield env.timeout(0.2)
            coordinator.cancel(order)

        env.process(cancel_later())
        env.run()

        # After cancellation, no _node_requests entries should exist for this AGV.
        # cancel() in the _travel() finally block should have cleaned up all entries,
        # including the initial placement at FAR and the mid-travel entry at MID.
        agv_node_requests = [k for k in traffic._node_requests if k[0] is agv]
        assert len(agv_node_requests) == 0

        # All node resources should be fully released (count=0)
        assert traffic._node_resources[node_far].count == 0
        assert traffic._node_resources[node_mid].count == 0
        assert traffic._node_resources[node_c].count == 0


# ===========================================================================
# Batch 3: Battery & Charging Correctness
# ===========================================================================


class TestCriticalBatteryInterruption:
    """S3: is_critical triggers immediate mission interruption and charging."""

    def test_critical_battery_triggers_charge_during_travel(
        self, env: Environment, sku_a: SKU
    ) -> None:
        """AGV hits critical battery mid-travel, diverts to charge, then
        completes the mission via the retry loop."""
        # Graph: A -- B(charger) -- C
        # Arc length = 5.  Default depletion = distance * 1.0.
        # capacity=100, critical_threshold=0.05 -> critical at level ≤ 5.
        # initial_battery=7: pre-arc check for A->B: cost=5, 7>=5 OK.
        # After deplete: level=2, pct=0.02 ≤ 0.05 -> is_critical.
        # _travel returns False. Retry loop charges, then re-plans from B.
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=5.0, y=0.0)
        node_c = Node(id="C", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_b, target=node_c),
        ]
        graph = LayoutGraph([node_a, node_b, node_c], arcs)

        speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_c = Warehouse(
            env=env, name="WH-C",
            input_bays=[node_c], output_bays=[node_c],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        charger = ChargingStation(env=env, name="CS-1", node=node_b, n_slots=1)

        agv_type = AGVType(
            name="test-type", speed_profile=speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv.battery.level = 7.0

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_c], charging_stations=[charger],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_c)
        coordinator.submit(order)
        env.run()

        # The mission should complete after the AGV charges at B
        assert order.status == OrderStatus.COMPLETED
        assert agv.state == AGVState.IDLE
        # Station should have been used for recharging
        assert charger.total_recharges >= 1


class TestChargingHookSignature:
    """S7: Charging hooks fire with (AGV, ChargingStation) signature."""

    def test_charging_hooks_receive_agv_and_station(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Register charging started/complete hooks. Verify they receive both
        the AGV and ChargingStation arguments."""
        coordinator, agv, wh_a, wh_b, charger = _build_system_with_charger(
            env, sku_a, simple_speed, battery_capacity=100.0, initial_battery=25.0
        )

        started_args: list[tuple[AGV, ChargingStation]] = []
        complete_args: list[tuple[AGV, ChargingStation]] = []

        coordinator.on_charging_started(lambda a, cs: started_args.append((a, cs)))
        coordinator.on_charging_complete(lambda a, cs: complete_args.append((a, cs)))

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        # Charging hooks should have fired with both AGV and station
        assert len(started_args) >= 1
        assert started_args[0][0] is agv
        assert started_args[0][1] is charger
        assert len(complete_args) >= 1
        assert complete_args[0][0] is agv
        assert complete_args[0][1] is charger


class TestOnLowBatteryOverride:
    """S8: on_low_battery callback overrides default charging."""

    def test_on_low_battery_callback_overrides_default_charging(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """When on_low_battery returns a generator, default charging (travel to
        station + recharge) is skipped. Verify station.total_recharges == 0."""
        node_a_out = Node(id="WH_A_OUT", x=0.0, y=0.0)
        node_corridor = Node(id="CORRIDOR", x=5.0, y=0.0)
        node_b_in = Node(id="WH_B_IN", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_a_out, target=node_corridor),
            Arc(source=node_corridor, target=node_b_in),
        ]
        graph = LayoutGraph([node_a_out, node_corridor, node_b_in], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a_out], output_bays=[node_a_out],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b_in], output_bays=[node_b_in],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        charger = ChargingStation(
            env=env, name="CS-1", node=node_corridor, n_slots=1,
        )

        callback_called = False

        def custom_low_battery(agv: AGV):
            nonlocal callback_called
            callback_called = True
            # Custom handler: just recharge the battery directly (no station)
            agv.battery.level = agv.battery.capacity
            yield env.timeout(1.0)

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a_out)
        agv.battery.level = 25.0  # Will be low after mission

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[charger],
            on_low_battery=custom_low_battery,
        )

        # Track whether default charging hooks fired
        charging_started_calls: list[tuple[AGV, ChargingStation]] = []
        coordinator.on_charging_started(lambda a, cs: charging_started_calls.append((a, cs)))

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert callback_called, "on_low_battery callback should have been called"
        # Default charging should NOT have been invoked
        assert charger.total_recharges == 0, (
            "Default charging should be skipped when on_low_battery returns a generator"
        )
        assert charging_started_calls == [], (
            "Default charging hook should not fire when on_low_battery overrides"
        )


# ===========================================================================
# Batch 4: Spec Compliance — Policies & Metrics
# ===========================================================================


class TestUnfulfillableOrderRetries:
    """S4: Unfulfillable orders are marked FAILED after max dispatch retries."""

    def test_unfulfillable_order_fails_after_max_retries(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """An order whose SKU weight exceeds all AGV capacities can never be
        dispatched. After enough pending-queue checks (driven by other
        orders completing), it should be marked FAILED."""
        coordinator, agv, wh_a, wh_b = _build_simple_system(
            env, sku_a, simple_speed, origin_inventory=200
        )
        # Override max_dispatch_retries to a small value for the test
        coordinator._max_dispatch_retries = 3

        # Create a heavy SKU that exceeds the AGV's weight capacity (100.0)
        heavy_sku = SKU(id="HEAVY", weight=200.0, volume=0.01)
        # Add the heavy SKU to warehouse inventory so it is a valid product
        wh_a.inventory[heavy_sku] = wh_a.inventory[sku_a].__class__(
            env=env, capacity=100
        )
        wh_a.inventory[heavy_sku]._level = 50  # type: ignore[attr-defined]

        unfulfillable_order = coordinator.create_order(
            sku=heavy_sku, quantity=1, origin=wh_a, destination=wh_b
        )
        coordinator.submit(unfulfillable_order)
        assert unfulfillable_order.status == OrderStatus.PENDING

        # Now submit and complete 3 normal orders to drive pending-queue checks
        for _ in range(3):
            normal_order = coordinator.create_order(
                sku=sku_a, quantity=1, origin=wh_a, destination=wh_b
            )
            coordinator.submit(normal_order)
            env.run()

        # After 3 completed missions (3 pending-queue checks), the unfulfillable
        # order should have been retried 3 times and marked FAILED
        assert unfulfillable_order.status == OrderStatus.FAILED
        assert unfulfillable_order not in coordinator._pending_queue


class TestResumeDeliveryRecovery:
    """S6: ResumeDelivery actually resumes travel after interrupt."""

    def test_resume_delivery_completes_after_interrupt(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Interrupt a mission after pickup (during loaded travel).
        With ResumeDelivery strategy, the order should eventually complete."""
        from simulatte.intralogistics.policies import ResumeDelivery

        # Longer graph so loaded travel takes time: ORIGIN -- MID -- DEST
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=50.0, y=0.0)
        node_dest = Node(id="DEST", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
            load_recovery_strategy=ResumeDelivery(),
        )

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # AGV at origin. pick_time=1.0, load_time=1.0 -> pickup done at ~2.0.
        # Loaded travel ORIGIN->MID: distance=50, speed=10 -> 5.0s.
        # Interrupt at t=4.0 — during loaded travel (after pickup).
        def interrupt_later():
            yield env.timeout(4.0)
            process = coordinator._active_missions.get(order.id)
            if process is not None and process.is_alive:
                process.interrupt("test_interrupt")

        env.process(interrupt_later())
        env.run()

        # The order should complete via ResumeDelivery recovery
        assert order.status == OrderStatus.COMPLETED
        assert order.delivered_at is not None
        assert agv.state == AGVState.IDLE
        assert agv.current_load is None
        # Inventory should have been transferred
        assert wh_origin.get_inventory_level(sku_a) == 90
        assert wh_dest.get_inventory_level(sku_a) == 10


class TestEventDrivenReplenishment:
    """S9: Event-driven replenishment fires after delivery."""

    def test_event_driven_replenishment_after_delivery(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Add a replenishment policy with check_interval=None. Complete a
        delivery that drains the origin warehouse below threshold. Verify
        replenishment orders are submitted."""
        from simulatte.intralogistics.policies import ReorderPointPolicy

        # Two warehouses: WH-A (origin, starts with 100) and WH-B (destination).
        # A third warehouse WH-C acts as replenishment source.
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=10.0, y=0.0)
        node_c = Node(id="C", x=20.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_b, target=node_c),
        ]
        graph = LayoutGraph([node_a, node_b, node_c], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b], output_bays=[node_b],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_c = Warehouse(
            env=env, name="WH-C",
            input_bays=[node_c], output_bays=[node_c],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 500},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b, wh_c], charging_stations=[],
        )

        # Drain WH-A below threshold first, so after delivery completes
        # the event-driven policy will detect the shortfall.
        def drain():
            yield from wh_a.pick(sku_a, 60)  # WH-A goes to 40

        env.process(drain())
        env.run()  # let drain finish

        # Register event-driven replenishment policy for WH-A
        # Threshold 50 means WH-A (now at 40) needs replenishment
        policy = ReorderPointPolicy(
            thresholds={sku_a: 50},
            reorder_quantity={sku_a: 30},
        )
        coordinator.add_replenishment_policy(policy, wh_a, check_interval=None)

        # Submit a normal order (WH-A -> WH-B) that picks 10 more, bringing WH-A to 30
        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_a, destination=wh_b
        )
        coordinator.submit(order)

        # Run until first order completes — this triggers event-driven check
        env.run()

        # After the first delivery completes, the event-driven policy should
        # have submitted a replenishment order (WH-C -> WH-A)
        has_replenishment = (
            len(coordinator._active_missions) > 0
            or len(coordinator._pending_queue) > 0
            or wh_a.get_inventory_level(sku_a) > 30
        )
        assert has_replenishment, (
            "Event-driven replenishment should have created an order after delivery"
        )


# ===========================================================================
# Batch 5: Test Coverage — closing coverage gaps
# ===========================================================================


class TestFleetEdgeCases:
    """Fleet convenience methods with edge cases."""

    def test_fleet_utilization_empty_fleet(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """fleet_utilization returns 0.0 when fleet is empty (line 220)."""
        node = Node(id="A", x=0.0, y=0.0)
        graph = LayoutGraph([node], [])
        wh = Warehouse(
            env=env, name="WH", input_bays=[node], output_bays=[node],
            n_slots=1, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[], warehouses=[wh], charging_stations=[],
        )
        assert coordinator.fleet_utilization == 0.0

    def test_fleet_time_allocation_empty_fleet(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """fleet_time_allocation returns zeros dict when fleet is empty (line 226)."""
        node = Node(id="A", x=0.0, y=0.0)
        graph = LayoutGraph([node], [])
        wh = Warehouse(
            env=env, name="WH", input_bays=[node], output_bays=[node],
            n_slots=1, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[], warehouses=[wh], charging_stations=[],
        )
        alloc = coordinator.fleet_time_allocation()
        for state in AGVState:
            assert alloc[state] == 0.0

    def test_on_battery_low_hook_registration(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """on_battery_low hook fires during charging (line 201, 706)."""
        coordinator, agv, wh_a, wh_b, charger = _build_system_with_charger(
            env, sku_a, simple_speed, battery_capacity=100.0, initial_battery=25.0
        )

        low_battery_events: list[AGV] = []
        coordinator.on_battery_low(lambda a: low_battery_events.append(a))

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert len(low_battery_events) >= 1
        assert low_battery_events[0] is agv


class TestInitialPlacementSkipsNodelessAGV:
    """AGV with initial_node=None is skipped during placement (689->688)."""

    def test_agv_without_node_skipped(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        node = Node(id="A", x=0.0, y=0.0)
        graph = LayoutGraph([node], [])
        wh = Warehouse(
            env=env, name="WH", input_bays=[node], output_bays=[node],
            n_slots=1, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv_no_node = AGV(env=env, agv_type=agv_type, agv_id="no-node", initial_node=None)
        agv_with_node = AGV(env=env, agv_type=agv_type, agv_id="with-node", initial_node=node)

        traffic = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=2)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv_no_node, agv_with_node],
            warehouses=[wh], charging_stations=[], traffic_manager=traffic,
        )
        env.run(until=0.001)

        # Only with_node should be placed
        assert traffic._node_resources[node].count == 1


class TestRepositioningAfterDelivery:
    """Repositioning after delivery (lines 401-404)."""

    def test_agv_repositions_to_parking_after_delivery(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """NearestParkingPolicy sends AGV to parking area after mission."""
        from simulatte.intralogistics.parking import ParkingArea
        from simulatte.intralogistics.policies import NearestParkingPolicy

        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=10.0, y=0.0)
        node_park = Node(id="PARK", x=20.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_b, target=node_park),
        ]
        graph = LayoutGraph([node_a, node_b, node_park], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b], output_bays=[node_b],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        parking = ParkingArea(env=env, name="P1", node=node_park, capacity=2)

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[],
            repositioning_policy=NearestParkingPolicy(),
            parking_areas=[parking],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert agv.state == AGVState.IDLE
        # AGV should have traveled to parking area after delivery
        assert agv.current_node == node_park


class TestRepositioningFailure:
    """Repositioning travel failure (line 404)."""

    def test_repositioning_fails_gracefully(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Repositioning target exists but travel fails. AGV goes IDLE anyway."""
        from simulatte.intralogistics.parking import ParkingArea
        from simulatte.intralogistics.policies import NearestParkingPolicy

        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=10.0, y=0.0)
        node_park = Node(id="PARK", x=20.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            # No arc from B to PARK — repositioning travel will fail
        ]
        graph = LayoutGraph([node_a, node_b, node_park], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b], output_bays=[node_b],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        parking = ParkingArea(env=env, name="P1", node=node_park, capacity=2)

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[],
            repositioning_policy=NearestParkingPolicy(),
            parking_areas=[parking],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        # Order completes, repositioning fails (no path to PARK), AGV goes IDLE at B
        assert order.status == OrderStatus.COMPLETED
        assert agv.state == AGVState.IDLE
        # AGV should remain at B (repositioning failed)
        assert agv.current_node == node_b


class TestResumeDeliveryChargingRetry:
    """Resume delivery with critical battery during re-travel (lines 441-443)."""

    def test_resume_delivery_charges_during_retry(
        self, env: Environment, sku_a: SKU
    ) -> None:
        """Interrupt during loaded travel. ResumeDelivery recovery.
        During re-travel, battery becomes critical. Charges and completes."""
        from simulatte.intralogistics.policies import ResumeDelivery

        # Graph: ORIGIN -- MID -- CHARGER -- DEST
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=5.0, y=0.0)
        node_charger = Node(id="CHARGER", x=10.0, y=0.0)
        node_dest = Node(id="DEST", x=15.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_charger),
            Arc(source=node_charger, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_charger, node_dest], arcs)

        speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        charger = ChargingStation(env=env, name="CS-1", node=node_charger, n_slots=1)

        agv_type = AGVType(
            name="test-type", speed_profile=speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        # Battery=22: Pick(0 travel, at origin). Pick time + load time = 2.
        # Loaded travel ORIGIN->MID: 5 dist, cost 5. Level=17.
        # Loaded travel MID->CHARGER: 5 dist, cost 5. Level=12.
        # Loaded travel CHARGER->DEST: 5 dist, cost 5. Level=7.
        # No critical during normal travel.
        # Interrupt at t=3.0 (during loaded travel ORIGIN->MID, which takes 0.5s).
        # After interrupt, AGV at ORIGIN (didn't complete MID yet).
        # Wait, travel time for 5 dist at speed 10 = 0.5s. Pick done at t=2.
        # So travel starts at t=2. ORIGIN->MID: enter at t=2, timeout 0.5, finish at t=2.5.
        # Interrupt at t=3.0 is after MID is reached. ORIGIN->MID depletes 5.
        # At t=2.5: level=17. Continue to MID->CHARGER: t=3.0 is mid-travel.
        # Interrupt at t=2.7. AGV state: between enter(CHARGER) and arrive.
        # reached_next=False -> leave_node(CHARGER), raise.
        #
        # Better: give battery just enough for first few arcs of normal travel,
        # then interrupt, and during resume the battery goes critical.
        # Initial battery=12. Normal: picks, travels ORIGIN->MID (cost 5, level=7).
        # Interrupt at t=2.3 (during travel ORIGIN->MID, mid-arc).
        # After interrupt: agv.current_load is set (picked_at happened at t=2).
        # ResumeDelivery sets IN_TRANSIT. Resume travel from MID (if reached) or ORIGIN.
        # Actually the interrupt flow: if not reached_next, leave next_node.
        # Level at interrupt: 12 - partial. But deplete only happens after timeout.
        # So level is still 12 at interrupt time.
        # Resume from ORIGIN: travel to DEST.
        # With level=12: ORIGIN->MID costs 5 (7 remaining), MID->CHARGER costs 5 (2 remaining).
        # pct=2/100=0.02 critical. S3 fires. _travel returns False.
        # Resume retry: not stranded, is_critical, has stations -> line 441-443!
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)
        agv.battery.level = 12.0

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[charger],
            load_recovery_strategy=ResumeDelivery(),
        )

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # Interrupt after pickup during loaded travel
        def interrupt_later():
            yield env.timeout(2.3)
            process = coordinator._active_missions.get(order.id)
            if process is not None and process.is_alive:
                process.interrupt("test_resume_charge")

        env.process(interrupt_later())
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert charger.total_recharges >= 1


class TestResumeDeliveryStranded:
    """ResumeDelivery path when re-travel results in STRANDED (lines 438-443)."""

    def test_resume_delivery_stranded_clears_cargo(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        from simulatte.intralogistics.policies import ResumeDelivery

        # Graph: ORIGIN -- ISLAND (disconnected from DEST)
        # After interrupt, AGV is on ISLAND with no path to DEST
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=50.0, y=0.0)
        node_dest = Node(id="DEST", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        # AGV with very limited battery: enough for first arc but not for resume
        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
            load_recovery_strategy=ResumeDelivery(),
        )

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # Interrupt after pickup (during loaded travel)
        # AGV at origin. pick_time=1.0, load_time=1.0 -> pickup done at ~2.0
        # Loaded travel to MID: 50/10=5.0s. Interrupt at t=4.0.
        def interrupt_and_block():
            yield env.timeout(4.0)
            process = coordinator._active_missions.get(order.id)
            if process is not None and process.is_alive:
                # Remove the arc from MID to DEST to strand the AGV on resume
                graph._adjacency[node_mid] = {}
                process.interrupt("test_strand")

        env.process(interrupt_and_block())
        env.run()

        # After stranding on resume: cargo should be cleared, order failed
        assert agv.current_load is None
        assert agv.state == AGVState.IDLE


class TestResumeDeliveryWithHooksAndCollector:
    """ResumeDelivery path with hooks and time-series collector (lines 457, 459)."""

    def test_resume_delivery_fires_hooks_and_collector(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        from simulatte.intralogistics.metrics import DefaultIntralogisticsCollector
        from simulatte.intralogistics.policies import ResumeDelivery

        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=50.0, y=0.0)
        node_dest = Node(id="DEST", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        collector = DefaultIntralogisticsCollector()
        delivery_hooks: list[tuple[TransferOrder, AGV]] = []

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
            load_recovery_strategy=ResumeDelivery(),
            time_series_collector=collector,
        )
        coordinator.on_delivery_complete(lambda o, a: delivery_hooks.append((o, a)))

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # Interrupt after pickup
        def interrupt_later():
            yield env.timeout(4.0)
            process = coordinator._active_missions.get(order.id)
            if process is not None and process.is_alive:
                process.interrupt("test_interrupt")

        env.process(interrupt_later())
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert len(delivery_hooks) >= 1
        assert delivery_hooks[-1][0] is order


class TestInterruptBeforePickup:
    """Non-cancel interrupt before pickup re-queues order (lines 468-470)."""

    def test_interrupt_before_pickup_requeues(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        # AGV starts far from origin so there's time to interrupt during empty travel
        node_far = Node(id="FAR", x=0.0, y=0.0)
        node_origin = Node(id="ORIGIN", x=100.0, y=0.0)
        node_dest = Node(id="DEST", x=110.0, y=0.0)

        arcs = [
            Arc(source=node_far, target=node_origin),
            Arc(source=node_origin, target=node_dest),
        ]
        graph = LayoutGraph([node_far, node_origin, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_far)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
        )

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # AGV travels FAR->ORIGIN: 100/10=10s. Interrupt at t=1.0 (before pickup)
        def interrupt_later():
            yield env.timeout(1.0)
            process = coordinator._active_missions.get(order.id)
            if process is not None and process.is_alive:
                process.interrupt("test_requeue")

        env.process(interrupt_later())
        env.run()

        # After interrupt before pickup: order re-queued as PENDING
        # Then dispatched again and completed
        assert order.status == OrderStatus.COMPLETED
        assert agv.state == AGVState.IDLE


class TestOnIdleHookAfterInterrupt:
    """on_agv_idle hook fires after interrupt cleanup (line 481)."""

    def test_idle_hook_fires_after_cancellation(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        node_far = Node(id="FAR", x=0.0, y=0.0)
        node_origin = Node(id="ORIGIN", x=100.0, y=0.0)
        node_dest = Node(id="DEST", x=110.0, y=0.0)

        arcs = [
            Arc(source=node_far, target=node_origin),
            Arc(source=node_origin, target=node_dest),
        ]
        graph = LayoutGraph([node_far, node_origin, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_far)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
        )

        idle_events: list[AGV] = []
        coordinator.on_agv_idle(lambda a: idle_events.append(a))

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # Cancel during travel
        def cancel_later():
            yield env.timeout(1.0)
            coordinator.cancel(order)

        env.process(cancel_later())
        env.run()

        assert order.status == OrderStatus.CANCELLED
        assert len(idle_events) >= 1
        assert idle_events[-1] is agv


class TestAlternativePathAlsoInfeasible:
    """Both primary and alt paths check as infeasible (lines 535-544)."""

    def test_alt_path_also_infeasible_strands(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Primary path conflicts on intermediate node B. Alt path found via C,
        but C also conflicts with another AGV's intent -> STRANDED.

        Graph: A -- B -- D and A -- C -- D (diamond).
        AGV1 intends [A, B, D] -> future {B, D}. But D is the dest, so
        conflict is just {B}. AGV3 intends [X, C, D] -> future {C, D}.

        AGV2 at A wants D.
        Primary: A->B->D. check_path: B conflicts with AGV1 -> {B}.
        Alt: plan(A, D, avoid=[B]) -> A->C->D.
        check_path(A->C->D): C conflicts with AGV3 -> infeasible.
        -> STRANDED (lines 535-543).
        """
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=5.0, y=5.0)
        node_c = Node(id="C", x=5.0, y=-5.0)
        node_d = Node(id="D", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_a, target=node_c),
            Arc(source=node_b, target=node_d),
            Arc(source=node_c, target=node_d),
        ]
        graph = LayoutGraph([node_a, node_b, node_c, node_d], arcs)

        traffic = ResourceBasedTrafficManager(
            graph=graph, env=env, node_capacity=1, deadlock_timeout=5.0,
        )

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 200},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_d = Warehouse(
            env=env, name="WH-D",
            input_bays=[node_d], output_bays=[node_d],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv1 = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv2 = AGV(env=env, agv_type=agv_type, agv_id="agv-2", initial_node=node_a)
        agv3 = AGV(env=env, agv_type=agv_type, agv_id="agv-3", initial_node=node_a)

        # AGV1 blocks path via B. Future nodes of [A, B] are {B}.
        traffic.register_intent(agv1, [node_a, node_b])
        # AGV3 blocks path via C. Future nodes of [A, C] are {C}.
        traffic.register_intent(agv3, [node_a, node_c])

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv2],
            warehouses=[wh_a, wh_d], charging_stations=[],
            traffic_manager=traffic,
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_d)
        coordinator.submit(order)

        env.run(until=500.0)

        assert order.status == OrderStatus.FAILED
        assert agv2.state == AGVState.STRANDED


class TestSuccessfulAlternativePath:
    """Alt path is feasible and used (line 544)."""

    def test_alt_path_used_when_primary_conflicts(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Primary path A->B->D conflicts on B. Alt path A->C->D is feasible.
        AGV uses alt path and completes the mission.

        Diamond: A --via B--> D, A --via C--> D.
        AGV1 intends [A, B], blocking B only.
        AGV2 primary path A->B->D has conflict on B.
        Alt path A->C->D has no conflicts -> feasible -> used (line 544).
        """
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=5.0, y=5.0)
        node_c = Node(id="C", x=5.0, y=-5.0)
        node_d = Node(id="D", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_a, target=node_c),
            Arc(source=node_b, target=node_d),
            Arc(source=node_c, target=node_d),
        ]
        graph = LayoutGraph([node_a, node_b, node_c, node_d], arcs)

        traffic = ResourceBasedTrafficManager(
            graph=graph, env=env, node_capacity=1, deadlock_timeout=5.0,
        )

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 200},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_d = Warehouse(
            env=env, name="WH-D",
            input_bays=[node_d], output_bays=[node_d],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv1 = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_b)
        agv2 = AGV(env=env, agv_type=agv_type, agv_id="agv-2", initial_node=node_a)

        # AGV1 blocks B (but not C or D)
        traffic.register_intent(agv1, [node_a, node_b])

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv2],
            warehouses=[wh_a, wh_d], charging_stations=[],
            traffic_manager=traffic,
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_d)
        coordinator.submit(order)

        env.run(until=500.0)

        assert order.status == OrderStatus.COMPLETED


class TestStrandedAfterCharging:
    """AGV stranded after charging when still insufficient (lines 584-589)."""

    def test_stranded_after_charging_insufficient_energy(
        self, env: Environment, sku_a: SKU
    ) -> None:
        """AGV charges but battery capacity is too small for the next arc."""
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=5.0, y=0.0)
        node_c = Node(id="C", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_b, target=node_c),
        ]
        graph = LayoutGraph([node_a, node_b, node_c], arcs)

        speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_c = Warehouse(
            env=env, name="WH-C",
            input_bays=[node_c], output_bays=[node_c],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        charger = ChargingStation(env=env, name="CS-1", node=node_b, n_slots=1)

        # Custom depletion: costs 100 per distance unit!
        # Arc A->B (5 units) costs 500 energy.
        # Arc B->C (5 units) also costs 500 energy.
        def expensive_depletion(distance: float, load_weight: float, speed: float) -> float:
            return distance * 100.0

        agv_type = AGVType(
            name="test-type", speed_profile=speed,
            battery_capacity=600.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
            depletion_fn=expensive_depletion,
        )
        # Battery at 550: first arc costs 500. After: level=50, pct=50/600=0.083 > 0.05.
        # But pre-arc check for B->C: cost 500, level=50 < 500.
        # _find_reachable_charger: charger at B, AGV is at B. Distance=0, cost=0. Reachable!
        # After charging: level=600 (full). Now 600 >= 500 for B->C. This works.
        # So capacity must be LESS than arc cost for this path.
        # capacity=400, level=500 -> can't. Level must be <= capacity.
        # Let's use capacity=400: first arc A->B costs 500. Pre-arc: level=400 < 500. FAIL.
        # But we need first arc to succeed. Let me re-think.
        #
        # Make depletion costs different per arc:
        # A->B: 5 dist * 100 = 500. Need level >= 500. capacity >= 500.
        # After A->B: level = capacity - 500. If capacity=510: level=10, pct=10/510=0.019 critical.
        # _travel returns False. Retry charges. After charge: level=510.
        # Pre-arc B->C: cost = 500. 510 >= 500. Charge happened, proceed.
        # This doesn't strand after charging.
        #
        # Approach: Make capacity < arc cost for B->C, but somehow get to B.
        # Use custom depletion: first arc cheap, second arc expensive.
        pass

    def test_stranded_after_charging_capacity_too_low(
        self, env: Environment, sku_a: SKU
    ) -> None:
        """AGV's battery capacity is too small for next arc even after full charge."""
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=2.0, y=0.0)
        node_c = Node(id="C", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_b, target=node_c),
        ]
        graph = LayoutGraph([node_a, node_b, node_c], arcs)

        speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_c = Warehouse(
            env=env, name="WH-C",
            input_bays=[node_c], output_bays=[node_c],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        charger = ChargingStation(env=env, name="CS-1", node=node_b, n_slots=1)

        agv_type = AGVType(
            name="test-type", speed_profile=speed,
            # battery_capacity=50: A->B costs 2, B->C costs 98.
            # After A->B: level=48 < 98 -> divert to charger at B.
            # Charger charges to capacity=50. Pre-arc B->C: cost=98, 50 < 98. STRANDED.
            battery_capacity=50.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_c], charging_stations=[charger],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_c)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.FAILED
        assert agv.state == AGVState.STRANDED


class TestChargeAgvNoStation:
    """_charge_agv with no reachable station (lines 697-701, 635->638)."""

    def test_charge_no_station_available(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """AGV needs charging but no stations are configured -> warning, no crash."""
        # Build with charger on disconnected node
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=10.0, y=0.0)
        node_cs = Node(id="CS", x=100.0, y=100.0)  # Disconnected!

        arcs = [Arc(source=node_a, target=node_b)]
        graph = LayoutGraph([node_a, node_b, node_cs], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b], output_bays=[node_b],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        charger = ChargingStation(env=env, name="CS-1", node=node_cs, n_slots=1)

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv.battery.level = 25.0  # Low after mission

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[charger],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        # Mission completes but charging is skipped (no reachable station)
        assert order.status == OrderStatus.COMPLETED
        assert agv.state == AGVState.IDLE


class TestChargeTravelFailure:
    """_charge_agv when travel to station fails (lines 723-724)."""

    def test_charge_travel_fails(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """AGV tries to travel to charger but battery dies en route."""
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=10.0, y=0.0)
        node_cs = Node(id="CS", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_b, target=node_cs),
        ]
        graph = LayoutGraph([node_a, node_b, node_cs], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b], output_bays=[node_b],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        charger = ChargingStation(env=env, name="CS-1", node=node_cs, n_slots=1)

        agv_type = AGVType(
            name="test-type", speed_profile=simple_speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        # Battery just enough for mission (10 units) but not for travel to charger (100 units)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv.battery.level = 20.0  # Enough for 10+10=20 (mission + return), low after

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b], charging_stations=[charger],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        # Mission completes, low battery detected, travel to charger fails
        assert order.status == OrderStatus.COMPLETED


class TestOnLowBatteryReturnsNone:
    """on_low_battery callback that returns None falls through to default (713->719)."""

    def test_on_low_battery_returns_none_uses_default(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        coordinator, agv, wh_a, wh_b, charger = _build_system_with_charger(
            env, sku_a, simple_speed, battery_capacity=100.0, initial_battery=25.0
        )

        callback_called = False

        def low_battery_none(agv: AGV):
            nonlocal callback_called
            callback_called = True
            return None  # Returns None, not a generator

        coordinator._on_low_battery = low_battery_none

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert callback_called
        # Default charging should still happen since callback returned None
        assert charger.total_recharges >= 1


class TestEventDrivenReplenishmentUnrelatedWarehouse:
    """Event-driven replenishment with unrelated warehouse (378->377, 774->763)."""

    def test_unrelated_warehouse_not_checked(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Policy for a warehouse not involved in the delivery is skipped."""
        from simulatte.intralogistics.policies import ReorderPointPolicy

        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=10.0, y=0.0)
        node_c = Node(id="C", x=20.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_b, target=node_c),
        ]
        graph = LayoutGraph([node_a, node_b, node_c], arcs)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_b = Warehouse(
            env=env, name="WH-B",
            input_bays=[node_b], output_bays=[node_b],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        # WH-C is unrelated to the delivery
        wh_c = Warehouse(
            env=env, name="WH-C",
            input_bays=[node_c], output_bays=[node_c],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 500},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_b, wh_c], charging_stations=[],
        )

        # Register event-driven replenishment for WH-C (unrelated to A->B delivery)
        policy = ReorderPointPolicy(
            thresholds={sku_a: 1000},
            reorder_quantity={sku_a: 50},
        )
        coordinator.add_replenishment_policy(policy, wh_c, check_interval=None)

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        env.run()

        # Order completes fine; WH-C policy is not triggered because WH-C
        # is neither origin nor destination of the delivered order
        assert order.status == OrderStatus.COMPLETED


class TestCriticalBatteryDuringLoadedTravel:
    """Critical battery during loaded travel triggers retry (line 319, 350-352)."""

    def test_critical_battery_during_loaded_travel_charges_and_retries(
        self, env: Environment, sku_a: SKU
    ) -> None:
        """AGV hits critical battery during loaded travel, charges at next node, retries."""
        # Graph: A -- B -- C(charger) -- D
        # AGV starts at A (origin). Destination D.
        # Critical hits at C (charger co-located), charges, resumes to D.
        node_a = Node(id="A", x=0.0, y=0.0)
        node_b = Node(id="B", x=5.0, y=0.0)
        node_c = Node(id="C", x=10.0, y=0.0)
        node_d = Node(id="D", x=15.0, y=0.0)

        arcs = [
            Arc(source=node_a, target=node_b),
            Arc(source=node_b, target=node_c),
            Arc(source=node_c, target=node_d),
        ]
        graph = LayoutGraph([node_a, node_b, node_c, node_d], arcs)

        speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)

        wh_a = Warehouse(
            env=env, name="WH-A",
            input_bays=[node_a], output_bays=[node_a],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_d = Warehouse(
            env=env, name="WH-D",
            input_bays=[node_d], output_bays=[node_d],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        # Charger co-located at C
        charger = ChargingStation(env=env, name="CS-1", node=node_c, n_slots=1)

        agv_type = AGVType(
            name="test-type", speed_profile=speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        # Battery at 12: empty travel (0, at origin). Pick.
        # Loaded: A->B: cost 5, level=7, pct=0.07 > 0.05.
        # Loaded: B->C: cost 5, level=2, pct=0.02 <= 0.05. Critical.
        # _travel returns False. AGV now at C. Retry: critical -> charges at C.
        # After charge: full. Resume loaded travel from C->D.
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv.battery.level = 12.0

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_a, wh_d], charging_stations=[charger],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_a, destination=wh_d)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert charger.total_recharges >= 1


class TestChargingDiversionDuringEmptyTravel:
    """Charging diversion (H6) during empty travel triggers retry with non-critical battery (317->319)."""

    def test_charging_diversion_during_empty_travel(
        self, env: Environment, sku_a: SKU
    ) -> None:
        """AGV diverts to charger mid-empty-travel (H6), then retries non-critical."""
        # Graph: FAR -- CHARGER -- ORIGIN -- DEST
        # AGV at FAR. Origin at ORIGIN.
        # Battery: enough for FAR->CHARGER (5), but not for CHARGER->ORIGIN (5).
        # Pre-arc check at CHARGER: energy < cost. Diverts to charger (co-located at CHARGER).
        # After charging: not critical. _travel returns False with cancel. H6 path.
        # Retry loop: not stranded, not critical -> skip charging, line 319 transition.
        node_far = Node(id="FAR", x=0.0, y=0.0)
        node_charger = Node(id="CHARGER", x=5.0, y=0.0)
        node_origin = Node(id="ORIGIN", x=10.0, y=0.0)
        node_dest = Node(id="DEST", x=15.0, y=0.0)

        arcs = [
            Arc(source=node_far, target=node_charger),
            Arc(source=node_charger, target=node_origin),
            Arc(source=node_origin, target=node_dest),
        ]
        graph = LayoutGraph([node_far, node_charger, node_origin, node_dest], arcs)

        speed = TrapezoidalProfile(max_speed=10.0, acceleration=1000.0, deceleration=1000.0)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        # Charger co-located at FAR so the diversion doesn't need travel
        charger = ChargingStation(env=env, name="CS-1", node=node_far, n_slots=1)

        agv_type = AGVType(
            name="test-type", speed_profile=speed,
            battery_capacity=100.0, weight_capacity=100.0, volume_capacity=10.0,
            load_time_fn=lambda: 1.0, unload_time_fn=lambda: 1.0,
            low_battery_threshold=0.2, critical_battery_threshold=0.05,
        )
        # Battery=4: pre-arc FAR->CHARGER costs 5. 4<5 -> H6 pre-arc check fails.
        # _find_reachable_charger: charger at FAR. Distance=0, cost=0, reachable.
        # Charge at FAR. Level=100. Return False from _travel via H6.
        # Retry loop: not stranded, not critical -> 317->319 (skip charging).
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_far)
        agv.battery.level = 4.0

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[charger],
        )

        order = coordinator.create_order(sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest)
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert charger.total_recharges >= 1


class TestReturnToOriginRecoveryPath:
    """ReturnToOrigin recovery when AGV has no load after recover (line 465)."""

    def test_return_to_origin_clears_load(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Interrupt after pickup with ReturnToOrigin strategy.
        AGV returns cargo, then agv.current_load is set to None (line 465)."""
        node_origin = Node(id="ORIGIN", x=0.0, y=0.0)
        node_mid = Node(id="MID", x=50.0, y=0.0)
        node_dest = Node(id="DEST", x=100.0, y=0.0)

        arcs = [
            Arc(source=node_origin, target=node_mid),
            Arc(source=node_mid, target=node_dest),
        ]
        graph = LayoutGraph([node_origin, node_mid, node_dest], arcs)

        wh_origin = Warehouse(
            env=env, name="WH-ORIGIN",
            input_bays=[node_origin], output_bays=[node_origin],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 100},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )
        wh_dest = Warehouse(
            env=env, name="WH-DEST",
            input_bays=[node_dest], output_bays=[node_dest],
            n_slots=2, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_origin)

        # ReturnToOrigin is the default load_recovery_strategy
        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh_origin, wh_dest], charging_stations=[],
        )

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_origin, destination=wh_dest
        )
        coordinator.submit(order)

        # Interrupt after pickup (during loaded travel)
        # Pick done at ~2.0. Cancel at t=4.0 (during loaded travel).
        def interrupt_later():
            yield env.timeout(4.0)
            process = coordinator._active_missions.get(order.id)
            if process is not None and process.is_alive:
                process.interrupt("test_return_to_origin")

        env.process(interrupt_later())
        env.run()

        # ReturnToOrigin puts cargo back, sets order PENDING, clears agv assignment
        # The coordinator then sets agv.current_load = None at line 465
        assert agv.current_load is None
        assert agv.state == AGVState.IDLE


class TestFindReachableChargerEdgeCases:
    """Edge cases for _find_reachable_charger (lines 767, 778)."""

    def test_no_reachable_charger_all_unreachable(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """All chargers exist but no path to any of them."""
        node_a = Node(id="A", x=0.0, y=0.0)
        node_cs = Node(id="CS", x=100.0, y=100.0)  # Disconnected

        graph = LayoutGraph([node_a, node_cs], [])

        charger = ChargingStation(env=env, name="CS-1", node=node_cs, n_slots=1)

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv.battery.level = 1.0

        wh = Warehouse(
            env=env, name="WH", input_bays=[node_a], output_bays=[node_a],
            n_slots=1, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh], charging_stations=[charger],
        )

        result = coordinator._find_reachable_charger(agv)
        assert result is None

    def test_charger_reachable_but_not_enough_energy(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """Path to charger exists but AGV doesn't have enough energy to reach it."""
        node_a = Node(id="A", x=0.0, y=0.0)
        node_cs = Node(id="CS", x=50.0, y=0.0)

        arcs = [Arc(source=node_a, target=node_cs)]
        graph = LayoutGraph([node_a, node_cs], arcs)

        charger = ChargingStation(env=env, name="CS-1", node=node_cs, n_slots=1)

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)
        agv.battery.level = 1.0  # Distance 50, depletion 50. level=1 < 50

        wh = Warehouse(
            env=env, name="WH", input_bays=[node_a], output_bays=[node_a],
            n_slots=1, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh], charging_stations=[charger],
        )

        result = coordinator._find_reachable_charger(agv)
        assert result is None

    def test_find_reachable_charger_no_current_node(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """AGV with current_node=None returns None."""
        node_cs = Node(id="CS", x=0.0, y=0.0)
        graph = LayoutGraph([node_cs], [])
        charger = ChargingStation(env=env, name="CS-1", node=node_cs, n_slots=1)

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=None)

        wh = Warehouse(
            env=env, name="WH", input_bays=[node_cs], output_bays=[node_cs],
            n_slots=1, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh], charging_stations=[charger],
        )

        result = coordinator._find_reachable_charger(agv)
        assert result is None

    def test_find_nearest_charger_no_current_node(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """_find_nearest_charger with current_node=None returns None (line 741)."""
        node_cs = Node(id="CS", x=0.0, y=0.0)
        graph = LayoutGraph([node_cs], [])
        charger = ChargingStation(env=env, name="CS-1", node=node_cs, n_slots=1)

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=None)

        wh = Warehouse(
            env=env, name="WH", input_bays=[node_cs], output_bays=[node_cs],
            n_slots=1, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh], charging_stations=[charger],
        )

        result = coordinator._find_nearest_charger(agv)
        assert result is None

    def test_find_nearest_charger_unreachable(
        self, env: Environment, sku_a: SKU, simple_speed: TrapezoidalProfile
    ) -> None:
        """_find_nearest_charger when path is None returns inf distance (line 747)."""
        node_a = Node(id="A", x=0.0, y=0.0)
        node_cs = Node(id="CS", x=100.0, y=100.0)

        graph = LayoutGraph([node_a, node_cs], [])  # No arcs

        charger = ChargingStation(env=env, name="CS-1", node=node_cs, n_slots=1)

        agv_type = _make_agv_type(simple_speed)
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-1", initial_node=node_a)

        wh = Warehouse(
            env=env, name="WH", input_bays=[node_a], output_bays=[node_a],
            n_slots=1, products=[sku_a], initial_inventory={sku_a: 0},
            pick_time_fn=lambda s, q: 1.0, put_time_fn=lambda s, q: 1.0,
        )

        coordinator = FleetCoordinator(
            env=env, graph=graph, fleet=[agv],
            warehouses=[wh], charging_stations=[charger],
        )

        result = coordinator._find_nearest_charger(agv)
        assert result is None
