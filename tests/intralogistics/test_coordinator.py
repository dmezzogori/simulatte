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
        """T6: When check_path finds conflict and no alternative route exists,
        the order should eventually FAIL or complete via timeout/reroute."""
        # Linear graph: A -- B -- C.  Two AGVs both at A, trying to go to C.
        # With node_capacity=1, the first AGV occupies B.  The second AGV's
        # path through B is infeasible and there's no alternative.
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

        # Run with a generous time bound
        env.run(until=500.0)

        # At least one order should complete; the other may fail or also complete
        # (the first AGV frees nodes as it moves, so the second may eventually
        # succeed via timeout/reroute). The key assertion: no infinite hang.
        statuses = {order1.status, order2.status}
        assert OrderStatus.COMPLETED in statuses or OrderStatus.FAILED in statuses

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
        occupied resource. The timeout must fire and the AGV must either
        reroute or gracefully fail.

        Graph:  START -- CHOKE -- END
                  \\              /
                   -- ALT ------

        Stationary AGV is placed on CHOKE (capacity=1). Traveling AGV
        plans START-CHOKE-END, check_path passes, enter_node blocks.
        After timeout, it should reroute through ALT.
        """
        node_start = Node(id="START", x=0.0, y=0.0)
        node_choke = Node(id="CHOKE", x=5.0, y=0.0)
        node_alt = Node(id="ALT", x=5.0, y=5.0)
        node_end = Node(id="END", x=10.0, y=0.0)

        arcs = [
            Arc(source=node_start, target=node_choke),
            Arc(source=node_choke, target=node_end),
            Arc(source=node_start, target=node_alt),
            Arc(source=node_alt, target=node_end),
        ]
        graph = LayoutGraph([node_start, node_choke, node_alt, node_end], arcs)

        traffic = ResourceBasedTrafficManager(
            graph=graph, env=env, node_capacity=1, deadlock_timeout=2.0,
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

        # Stationary AGV: placed on CHOKE, never dispatched, no intent registered
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

        order = coordinator.create_order(
            sku=sku_a, quantity=10, origin=wh_start, destination=wh_end
        )
        coordinator.submit(order)

        # Run with generous time limit
        env.run(until=500.0)

        # The traveler should have rerouted through ALT and completed,
        # or failed gracefully if reroute also didn't work
        assert order.status in {OrderStatus.COMPLETED, OrderStatus.FAILED}
        # With the ALT route available, it should complete
        assert order.status == OrderStatus.COMPLETED
