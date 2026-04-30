from __future__ import annotations

import math
from enum import Enum, auto
from typing import TYPE_CHECKING

import simpy

from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.metrics import EMAOrderMetrics
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.pathfinding import DijkstraPlanner
from simulatte.intralogistics.policies import (
    NearestIdleStrategy,
    RepositioningContext,
    ReturnToOrigin,
    StayInPlace,
)
from simulatte.intralogistics.traffic import FreeTrafficManager

if TYPE_CHECKING:
    from collections.abc import Callable

    from simpy.events import ProcessGenerator

    from simulatte.environment import Environment
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.charging import ChargingStation
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.metrics import (
        IntralogisticsTimeSeriesCollector,
        OrderMetricsCollector,
    )
    from simulatte.intralogistics.parking import ParkingArea
    from simulatte.intralogistics.pathfinding import PathPlanner
    from simulatte.intralogistics.policies import (
        DispatchStrategy,
        LoadRecoveryStrategy,
        ReplenishmentPolicy,
        RepositioningPolicy,
    )
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.traffic import TrafficManager
    from simulatte.intralogistics.warehouse import Warehouse


class _TravelOutcome(Enum):
    ARRIVED = auto()
    RETRY_FROM_CURRENT_POSITION = auto()
    MISSION_FAILED = auto()
    BATTERY_STRANDED = auto()


class _EnterOutcome(Enum):
    ENTERED = auto()
    REROUTE = auto()
    GAVE_UP = auto()


class FleetCoordinator:
    """Central orchestrator for AGV fleet operations and mission lifecycle.

    Manages transfer orders from submission through dispatch, travel, pick,
    transit, deliver, and completion.  Analogous to ``ShopFloor`` for
    production simulations but focused on warehouse-to-warehouse AGV transport.
    """

    def __init__(
        self,
        *,
        env: Environment,
        graph: LayoutGraph,
        fleet: list[AGV],
        warehouses: list[Warehouse],
        charging_stations: list[ChargingStation],
        parking_areas: list[ParkingArea] | None = None,
        traffic_manager: TrafficManager | None = None,
        path_planner: PathPlanner | None = None,
        dispatch_strategy: DispatchStrategy | None = None,
        repositioning_policy: RepositioningPolicy | None = None,
        load_recovery_strategy: LoadRecoveryStrategy | None = None,
        order_metrics_collector: OrderMetricsCollector | None = None,
        time_series_collector: IntralogisticsTimeSeriesCollector | None = None,
        on_low_battery: Callable[[AGV], ProcessGenerator | None] | None = None,
        max_dispatch_retries: int = 10,
    ) -> None:
        self.env = env
        self.graph = graph
        self.fleet = fleet
        self.warehouses = warehouses
        self.charging_stations = charging_stations
        self.parking_areas = parking_areas or []

        self._traffic_manager: TrafficManager = traffic_manager or FreeTrafficManager()
        self._path_planner: PathPlanner = path_planner or DijkstraPlanner()
        self._dispatch_strategy: DispatchStrategy = dispatch_strategy or NearestIdleStrategy()
        self._repositioning_policy: RepositioningPolicy = repositioning_policy or StayInPlace()
        self._load_recovery_strategy: LoadRecoveryStrategy = load_recovery_strategy or ReturnToOrigin()
        self._order_metrics_collector: OrderMetricsCollector = order_metrics_collector or EMAOrderMetrics()
        self._time_series_collector: IntralogisticsTimeSeriesCollector | None = time_series_collector
        self._on_low_battery = on_low_battery
        self._max_dispatch_retries = max_dispatch_retries
        self._dispatch_retries: dict[str, int] = {}
        self._pending_retry_scheduled = False
        self._pending_retry_delay = 0.001

        # Event-driven replenishment policies (checked after each pick)
        self._event_driven_policies: list[tuple[ReplenishmentPolicy, Warehouse]] = []

        # Internal state (keyed by order.id because TransferOrder is unhashable)
        self._active_missions: dict[str, simpy.Process] = {}
        self._agv_mission: dict[AGV, TransferOrder] = {}
        self._pending_queue: list[TransferOrder] = []
        self._low_battery_flags: set[AGV] = set()

        # H5: Track inventory deducted but not yet loaded onto AGV.
        # Maps order.id -> (warehouse, sku, quantity) for rollback on interrupt.
        self._committed_picks: dict[str, tuple[Warehouse, SKU, int]] = {}

        # Lifecycle hook registries
        self._hooks_on_order_submitted: list[Callable[[TransferOrder], None]] = []
        self._hooks_on_order_dispatched: list[Callable[[TransferOrder, AGV], None]] = []
        self._hooks_on_pickup_complete: list[Callable[[TransferOrder, AGV], None]] = []
        self._hooks_on_delivery_complete: list[Callable[[TransferOrder, AGV], None]] = []
        self._hooks_on_battery_low: list[Callable[[AGV], None]] = []
        self._hooks_on_charging_started: list[Callable[[AGV, ChargingStation], None]] = []
        self._hooks_on_charging_complete: list[Callable[[AGV, ChargingStation], None]] = []
        self._hooks_on_agv_idle: list[Callable[[AGV], None]] = []

        # S1: Initial AGV placement — register starting positions with traffic manager
        self.env.process(self._initial_placement())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_order(
        self,
        *,
        sku: SKU,
        quantity: int,
        origin: Warehouse,
        destination: Warehouse,
        **kwargs: object,
    ) -> TransferOrder:
        """Factory method that creates a ``TransferOrder`` with ``created_at`` set to now."""
        return TransferOrder(
            sku=sku,
            quantity=quantity,
            origin=origin,
            destination=destination,
            created_at=self.env.now,
            **kwargs,  # type: ignore[arg-type]
        )

    def submit(self, order: TransferOrder) -> None:
        """Submit an order for dispatch.

        If an idle AGV is available, the mission is spawned immediately.
        Otherwise the order enters ``_pending_queue``.
        """
        self.env.debug(
            f"Order {order.id} submitted (sku={order.sku.id}, qty={order.quantity})",
            component="FleetCoordinator",
        )

        # Fire hooks
        for cb in self._hooks_on_order_submitted:
            cb(order)
        if self._time_series_collector is not None:
            self._time_series_collector.on_order_submitted(self, order)

        agv = self._dispatch_strategy.select(order, self.fleet, self.graph)
        if agv is not None:
            self._dispatch(order, agv)
        else:
            order.status = OrderStatus.PENDING
            self._pending_queue.append(order)
            self._ensure_pending_retry_loop()
            self.env.debug(
                f"Order {order.id} queued (no idle AGV)",
                component="FleetCoordinator",
            )

    def cancel(self, order: TransferOrder) -> None:
        """Cancel an active or pending order."""
        # If pending, just remove from queue
        if order in self._pending_queue:
            self._pending_queue.remove(order)
            order.status = OrderStatus.CANCELLED
            self.env.debug(f"Order {order.id} cancelled (was pending)", component="FleetCoordinator")
            return

        # If active, interrupt the mission process
        process = self._active_missions.get(order.id)
        if process is not None and process.is_alive:
            process.interrupt("cancelled")
        order.status = OrderStatus.CANCELLED
        self.env.debug(f"Order {order.id} cancelled", component="FleetCoordinator")

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def on_order_submitted(self, callback: Callable[[TransferOrder], None]) -> None:
        self._hooks_on_order_submitted.append(callback)

    def on_order_dispatched(self, callback: Callable[[TransferOrder, AGV], None]) -> None:
        self._hooks_on_order_dispatched.append(callback)

    def on_pickup_complete(self, callback: Callable[[TransferOrder, AGV], None]) -> None:
        self._hooks_on_pickup_complete.append(callback)

    def on_delivery_complete(self, callback: Callable[[TransferOrder, AGV], None]) -> None:
        self._hooks_on_delivery_complete.append(callback)

    def on_battery_low(self, callback: Callable[[AGV], None]) -> None:
        self._hooks_on_battery_low.append(callback)

    def on_charging_started(self, callback: Callable[[AGV, ChargingStation], None]) -> None:
        self._hooks_on_charging_started.append(callback)

    def on_charging_complete(self, callback: Callable[[AGV, ChargingStation], None]) -> None:
        self._hooks_on_charging_complete.append(callback)

    def on_agv_idle(self, callback: Callable[[AGV], None]) -> None:
        self._hooks_on_agv_idle.append(callback)

    # ------------------------------------------------------------------
    # Fleet convenience
    # ------------------------------------------------------------------

    @property
    def fleet_utilization(self) -> float:
        """Average utilization across the fleet."""
        if not self.fleet:
            return 0.0
        return sum(agv.utilization() for agv in self.fleet) / len(self.fleet)

    def fleet_time_allocation(self) -> dict[AGVState, float]:
        """Average time-allocation percentages across the fleet."""
        if not self.fleet:
            return {s: 0.0 for s in AGVState}
        combined: dict[AGVState, float] = {s: 0.0 for s in AGVState}
        for agv in self.fleet:
            alloc = agv.time_allocation()
            for s, pct in alloc.items():
                combined[s] += pct
        n = len(self.fleet)
        return {s: v / n for s, v in combined.items()}

    def agv_report(self) -> list[dict[str, object]]:
        """Per-AGV summary."""
        report: list[dict[str, object]] = []
        for agv in self.fleet:
            report.append(
                {
                    "agv_id": agv.agv_id,
                    "state": agv.state.name,
                    "battery_pct": agv.battery.level_pct,
                    "current_node": agv.current_node.id if agv.current_node else None,
                    "utilization": agv.utilization(),
                }
            )
        return report

    # ------------------------------------------------------------------
    # Replenishment
    # ------------------------------------------------------------------

    def add_replenishment_policy(
        self,
        policy: ReplenishmentPolicy,
        warehouse: Warehouse,
        check_interval: float | None = None,
    ) -> None:
        """Wire a replenishment policy.

        If ``check_interval`` is set, spawn a periodic SimPy process.
        Otherwise, the policy is checked after every delivery that involves
        the monitored warehouse (event-driven).
        """
        if check_interval is not None:
            self.env.process(self._replenishment_loop(policy, warehouse, check_interval))
        else:
            self._event_driven_policies.append((policy, warehouse))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _transition_agv(self, agv: AGV, new_state: AGVState) -> None:
        """Transition an AGV to *new_state* and notify the time-series collector."""
        old_state = agv.state
        agv.transition_to(new_state)
        if self._time_series_collector is not None:
            self._time_series_collector.on_agv_state_changed(self, agv, old_state, new_state)

    def _dispatch(self, order: TransferOrder, agv: AGV) -> None:
        """Spawn a mission process for the given order/AGV pair.

        Eagerly sets the order status and AGV state so that subsequent
        ``submit()`` calls in the same simulation step see the AGV as busy.
        """
        order.assigned_agv = agv
        order.status = OrderStatus.DISPATCHED
        order.dispatched_at = self.env.now
        self._transition_agv(agv, AGVState.TRAVELING_EMPTY)

        process = self.env.process(self._run_mission(order, agv))
        self._active_missions[order.id] = process
        self._agv_mission[agv] = order

        # Fire hooks
        for cb in self._hooks_on_order_dispatched:
            cb(order, agv)
        if self._time_series_collector is not None:
            self._time_series_collector.on_order_dispatched(self, order, agv)

    def _run_mission(self, order: TransferOrder, agv: AGV) -> ProcessGenerator:
        """Full mission lifecycle as a SimPy process."""
        try:
            # 1. Travel empty to origin output bay
            # (order.status, dispatched_at, and AGV state are set eagerly in _dispatch)
            origin_output_bay = order.origin.nearest_output_bay(agv.current_node, self.graph)
            while True:
                outcome = yield from self._travel(agv, agv.current_node, origin_output_bay, loaded=False)
                if outcome is _TravelOutcome.ARRIVED:
                    break
                if outcome is _TravelOutcome.BATTERY_STRANDED:
                    order.status = OrderStatus.FAILED
                    return
                if outcome is _TravelOutcome.MISSION_FAILED:
                    order.status = OrderStatus.FAILED
                    self._transition_agv(agv, AGVState.IDLE)
                    return
                # Charging diversion or critical battery — charge first if needed
                if agv.battery.is_critical and self.charging_stations:
                    yield from self._charge_agv(agv)
                self._transition_agv(agv, AGVState.TRAVELING_EMPTY)

            # 2. Pick
            order.status = OrderStatus.PICKING
            self._transition_agv(agv, AGVState.WAITING_LOAD)
            self._committed_picks[order.id] = (order.origin, order.sku, order.quantity)
            yield from order.origin.pick(order.sku, order.quantity)
            agv.current_load = {order.sku: order.quantity}
            del self._committed_picks[order.id]
            order.picked_at = self.env.now
            yield self.env.timeout(agv.agv_type.load_time_fn())

            # Fire pickup hooks
            for cb in self._hooks_on_pickup_complete:
                cb(order, agv)
            if self._time_series_collector is not None:
                self._time_series_collector.on_pickup_complete(self, order, agv)

            # 3. Travel loaded to destination input bay
            order.status = OrderStatus.IN_TRANSIT
            self._trigger_event_driven_replenishment(order.origin)
            self._transition_agv(agv, AGVState.TRAVELING_LOADED)

            dest_input_bay = order.destination.nearest_input_bay(agv.current_node, self.graph)
            while True:
                outcome = yield from self._travel(agv, agv.current_node, dest_input_bay, loaded=True)
                if outcome is _TravelOutcome.ARRIVED:
                    break
                if outcome is _TravelOutcome.BATTERY_STRANDED:
                    order.status = OrderStatus.FAILED
                    return
                if outcome is _TravelOutcome.MISSION_FAILED:
                    order.status = OrderStatus.FAILED
                    self._transition_agv(agv, AGVState.IDLE)
                    return
                # Charging diversion or critical battery — charge first if needed
                if agv.battery.is_critical and self.charging_stations:
                    yield from self._charge_agv(agv)
                self._transition_agv(agv, AGVState.TRAVELING_LOADED)

            # 4. Deliver
            order.status = OrderStatus.DELIVERING
            self._transition_agv(agv, AGVState.WAITING_UNLOAD)
            yield from order.destination.put(order.sku, order.quantity)
            agv.current_load = None
            yield self.env.timeout(agv.agv_type.unload_time_fn())
            order.delivered_at = self.env.now
            order.status = OrderStatus.COMPLETED

            # 5. Post-mission
            self._order_metrics_collector.record(order)

            for cb in self._hooks_on_delivery_complete:
                cb(order, agv)
            if self._time_series_collector is not None:
                self._time_series_collector.on_delivery_complete(self, order, agv)

            self.env.debug(
                f"Order {order.id} completed at t={self.env.now:.2f}",
                component="FleetCoordinator",
            )

            # Battery check after mission
            if agv.battery.is_low and self.charging_stations:
                yield from self._charge_agv(agv)
            else:
                # Repositioning
                repo_ctx = RepositioningContext(
                    graph=self.graph,
                    parking_areas=self.parking_areas,
                    charging_stations=self.charging_stations,
                    fleet=self.fleet,
                )
                target = self._repositioning_policy.reposition(agv, repo_ctx)
                if target is not None and target != agv.current_node:
                    self._transition_agv(agv, AGVState.TRAVELING_EMPTY)
                    outcome = yield from self._travel(agv, agv.current_node, target, loaded=False)
                    if outcome is _TravelOutcome.BATTERY_STRANDED:
                        pass

            # Go IDLE
            self._transition_agv(agv, AGVState.IDLE)
            for cb in self._hooks_on_agv_idle:
                cb(agv)

        except simpy.Interrupt:
            self.env.debug(
                f"Order {order.id} interrupted (agv={agv.agv_id})",
                component="FleetCoordinator",
            )

            # H5: Roll back committed but unloaded pick (inventory deducted
            # inside warehouse.pick() but not yet assigned to agv.current_load).
            committed = self._committed_picks.pop(order.id, None)
            if committed is not None:
                wh, sku, qty = committed
                self.env.process(wh.put(sku, qty))

            if order.status != OrderStatus.CANCELLED:
                # Not an explicit cancellation — handle gracefully
                if agv.current_load is not None:
                    # Has cargo — delegate to load recovery strategy
                    yield from self._load_recovery_strategy.recover(order, agv, self)

                    if order.status == OrderStatus.IN_TRANSIT and agv.current_load is not None:
                        # S6: ResumeDelivery — re-travel to destination from current position
                        dest_input_bay = order.destination.nearest_input_bay(agv.current_node, self.graph)
                        self._transition_agv(agv, AGVState.TRAVELING_LOADED)
                        while True:
                            outcome = yield from self._travel(agv, agv.current_node, dest_input_bay, loaded=True)
                            if outcome is _TravelOutcome.ARRIVED:
                                break
                            if outcome is _TravelOutcome.BATTERY_STRANDED:
                                order.status = OrderStatus.FAILED
                                break
                            if outcome is _TravelOutcome.MISSION_FAILED:
                                order.status = OrderStatus.FAILED
                                self._transition_agv(agv, AGVState.IDLE)
                                break
                            if agv.battery.is_critical and self.charging_stations:
                                yield from self._charge_agv(agv)
                            self._transition_agv(agv, AGVState.TRAVELING_LOADED)

                        if order.status == OrderStatus.IN_TRANSIT:
                            # Successfully re-traveled — complete delivery
                            order.status = OrderStatus.DELIVERING
                            self._transition_agv(agv, AGVState.WAITING_UNLOAD)
                            yield from order.destination.put(order.sku, order.quantity)
                            agv.current_load = None
                            yield self.env.timeout(agv.agv_type.unload_time_fn())
                            order.delivered_at = self.env.now
                            order.status = OrderStatus.COMPLETED
                            self._order_metrics_collector.record(order)

                            for cb in self._hooks_on_delivery_complete:
                                cb(order, agv)
                            if self._time_series_collector is not None:
                                self._time_series_collector.on_delivery_complete(self, order, agv)
                        else:
                            # Resume failed (STRANDED) — clear cargo
                            agv.current_load = None
                    else:
                        # ReturnToOrigin or similar — cargo was returned/cleared
                        agv.current_load = None
                else:
                    # Before pickup — re-queue
                    order.status = OrderStatus.PENDING
                    order.assigned_agv = None
                    self._pending_queue.append(order)
                    self._ensure_pending_retry_loop()
            else:
                # Explicit cancellation — clear the AGV load if any
                if agv.current_load is not None:
                    # Return inventory to origin before clearing load
                    for sku, qty in agv.current_load.items():
                        self.env.process(order.origin.put(sku, qty))
                    agv.current_load = None

            self._transition_agv(agv, AGVState.IDLE)
            for cb in self._hooks_on_agv_idle:
                cb(agv)

        finally:
            # Cleanup mission tracking
            self._active_missions.pop(order.id, None)
            self._agv_mission.pop(agv, None)

            # Check pending queue
            self._check_pending_queue()

    def _travel(
        self,
        agv: AGV,
        from_node: Node,
        to_node: Node,
        loaded: bool,
    ) -> ProcessGenerator:
        """Move the AGV along the graph from ``from_node`` to ``to_node``.

        Returns a ``_TravelOutcome`` describing whether the AGV arrived,
        should retry from its current position, failed for mission-routing
        reasons, or became battery-stranded.
        """
        if from_node == to_node:
            return _TravelOutcome.ARRIVED

        avoid_nodes: list[Node] | None = None

        while True:
            path = self._path_planner.plan(self.graph, from_node, to_node, avoid=avoid_nodes)
            if path is None:
                self.env.error(
                    f"No path from {from_node.id} to {to_node.id} for {agv.agv_id}",
                    component="FleetCoordinator",
                )
                return _TravelOutcome.MISSION_FAILED

            while True:
                result = self._traffic_manager.check_path(agv, path)
                if result.feasible:
                    break

                if result.conflict_nodes:
                    alt_path = self._path_planner.plan(self.graph, from_node, to_node, avoid=result.conflict_nodes)
                    if alt_path is None:
                        self.env.error(
                            f"No alternative path from {from_node.id} to {to_node.id} for {agv.agv_id}",
                            component="FleetCoordinator",
                        )
                        return _TravelOutcome.MISSION_FAILED
                    alt_result = self._traffic_manager.check_path(agv, alt_path)
                    if alt_result.feasible:
                        path = alt_path
                        break
                    if alt_result.delay_until is not None:
                        wait = max(0.0, alt_result.delay_until - self.env.now)
                        if wait > 0:
                            yield self.env.timeout(wait)
                        path = alt_path
                        continue
                    self.env.error(
                        f"Alternative path also infeasible from {from_node.id} to {to_node.id} for {agv.agv_id}",
                        component="FleetCoordinator",
                    )
                    return _TravelOutcome.MISSION_FAILED

                if result.delay_until is not None:
                    wait = max(0.0, result.delay_until - self.env.now)
                    if wait > 0:
                        yield self.env.timeout(wait)
                    continue

                self.env.error(
                    f"Infeasible path from {from_node.id} to {to_node.id} for {agv.agv_id} "
                    f"with no reroute or delay guidance",
                    component="FleetCoordinator",
                )
                return _TravelOutcome.MISSION_FAILED

            self._traffic_manager.register_intent(agv, path)
            deadlock_timeout = self._traffic_manager.deadlock_timeout
            reroute_requested = False

            try:
                for i in range(len(path) - 1):
                    current = path[i]
                    next_node = path[i + 1]

                    distance = math.hypot(next_node.x - current.x, next_node.y - current.y)
                    if loaded and agv.current_load:
                        load_weight = sum(sku.weight * qty for sku, qty in agv.current_load.items())
                    else:
                        load_weight = 0.0

                    arc = self.graph.arc_between(current, next_node)
                    arc_speed_limit = arc.speed_limit if arc is not None else None
                    travel_time = agv.agv_type.speed_profile.travel_time(
                        distance, load_weight, agv.battery.level_pct, speed_limit=arc_speed_limit
                    )
                    avg_speed = distance / travel_time if travel_time > 0 else 0.0
                    energy_cost = agv.battery.estimate_energy(distance, load_weight, avg_speed)

                    if agv.battery.level < energy_cost:
                        charger = self._find_reachable_charger(agv)
                        if charger is not None:
                            prior_state = agv.state
                            yield from self._charge_agv(agv, charger)
                            self._transition_agv(agv, prior_state)
                            if agv.battery.level < energy_cost:
                                self._transition_agv(agv, AGVState.STRANDED)
                                self.env.error(
                                    f"{agv.agv_id} STRANDED at {current.id} — insufficient energy even after charging",
                                    component="FleetCoordinator",
                                )
                                return _TravelOutcome.BATTERY_STRANDED
                            self._traffic_manager.cancel(agv)
                            return _TravelOutcome.RETRY_FROM_CURRENT_POSITION

                        self._transition_agv(agv, AGVState.STRANDED)
                        self.env.error(
                            f"{agv.agv_id} STRANDED at {current.id} — no reachable charger",
                            component="FleetCoordinator",
                        )
                        return _TravelOutcome.BATTERY_STRANDED

                    reached_next = False
                    try:
                        if deadlock_timeout is not None:
                            enter_outcome = yield from self._enter_with_timeout(
                                agv, next_node, deadlock_timeout, destination=to_node
                            )
                            if enter_outcome is _EnterOutcome.REROUTE:
                                avoid_nodes = [next_node]
                                reroute_requested = True
                                break
                            if enter_outcome is _EnterOutcome.GAVE_UP:
                                self.env.error(
                                    f"{agv.agv_id} could not enter node {next_node.id} after deadlock retries",
                                    component="FleetCoordinator",
                                )
                                return _TravelOutcome.MISSION_FAILED
                        else:
                            yield from self._traffic_manager.enter_node(agv, next_node)

                        yield self.env.timeout(travel_time)
                        agv.battery.deplete(distance, load_weight, avg_speed)
                        self._traffic_manager.leave_node(agv, current)
                        agv.current_node = next_node
                        reached_next = True

                        if agv.battery.is_critical:
                            self._traffic_manager.cancel(agv)
                            return _TravelOutcome.RETRY_FROM_CURRENT_POSITION
                    except simpy.Interrupt:
                        if not reached_next:  # pragma: no cover
                            self._traffic_manager.leave_node(agv, next_node)
                        raise
            finally:
                self._traffic_manager.cancel(agv)

            if reroute_requested:
                from_node = agv.current_node
                continue

            return _TravelOutcome.ARRIVED

    def _enter_with_timeout(
        self,
        agv: AGV,
        node: Node,
        timeout: float,
        *,
        destination: Node,
        _max_retries: int = 3,
    ) -> ProcessGenerator:
        """Try to enter ``node`` with timeout, reroute, and priority backoff.

        On timeout, first try a reroute that avoids the blocked node. If no
        alternative exists, wait with exponential backoff and retry. Returns an
        ``_EnterOutcome`` indicating whether the node was entered, travel should
        be rerouted, or the coordinator should give up.
        """
        for attempt in range(_max_retries):
            enter_proc = self.env.process(self._traffic_manager.enter_node(agv, node))
            timer = self.env.timeout(timeout)
            yield enter_proc | timer

            if enter_proc.triggered:
                return _EnterOutcome.ENTERED

            if enter_proc.is_alive:  # pragma: no cover
                enter_proc.interrupt("deadlock_timeout")
            self._traffic_manager.cancel(agv)

            current_node = agv.current_node
            if current_node is not None:
                alt_path = self._path_planner.plan(self.graph, current_node, destination, avoid=[node])
                if alt_path is not None:
                    return _EnterOutcome.REROUTE

            priority = self._traffic_manager.priority(agv)
            backoff_multiplier = attempt + 1 if priority > 0 else 2**attempt
            yield self.env.timeout(timeout * backoff_multiplier)

        return _EnterOutcome.GAVE_UP

    def _initial_placement(self) -> ProcessGenerator:
        """Register starting positions of all AGVs with the traffic manager (S1)."""
        for agv in self.fleet:
            if agv.current_node is not None:
                yield from self._traffic_manager.place(agv, agv.current_node)

    def _charge_agv(self, agv: AGV, station: ChargingStation | None = None) -> ProcessGenerator:
        """Navigate to a charging station and recharge."""
        if station is None:
            station = self._find_nearest_charger(agv)
        if station is None:
            self.env.warning(
                f"No charging station available for {agv.agv_id}",
                component="FleetCoordinator",
            )
            return

        self._low_battery_flags.add(agv)

        for cb in self._hooks_on_battery_low:
            cb(agv)

        # Fire the constructor-supplied low-battery callback (may be a generator).
        # If the callback returns a generator, yield from it and return — the
        # callback overrides the default charging behaviour.
        if self._on_low_battery is not None:
            result = self._on_low_battery(agv)
            if result is not None:
                yield from result
                self._low_battery_flags.discard(agv)
                return

        # Travel to charging station
        if agv.current_node is None:
            self._low_battery_flags.discard(agv)
            return
        if agv.current_node != station.node:
            self._transition_agv(agv, AGVState.TRAVELING_EMPTY)
            outcome = yield from self._travel(agv, agv.current_node, station.node, loaded=False)
            if outcome is not _TravelOutcome.ARRIVED:
                self._low_battery_flags.discard(agv)
                return

        # Charge
        self._transition_agv(agv, AGVState.CHARGING)
        for cb in self._hooks_on_charging_started:
            cb(agv, station)

        yield from station.recharge(agv)

        for cb in self._hooks_on_charging_complete:
            cb(agv, station)

        self._low_battery_flags.discard(agv)

    def _find_nearest_charger(self, agv: AGV) -> ChargingStation | None:
        """Find the nearest charging station by graph distance."""
        if not self.charging_stations or agv.current_node is None:
            return None

        def _distance(cs: ChargingStation) -> float:
            assert agv.current_node is not None
            path = self.graph.shortest_path(agv.current_node, cs.node)
            if path is None:
                return float("inf")
            return sum(math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y) for i in range(len(path) - 1))

        best = min(self.charging_stations, key=_distance)
        d = _distance(best)
        return best if d < float("inf") else None

    def _find_reachable_charger(self, agv: AGV) -> ChargingStation | None:
        """Find a charger the AGV can reach with its current battery."""
        if not self.charging_stations or agv.current_node is None:
            return None

        reachable: list[tuple[float, ChargingStation]] = []
        for cs in self.charging_stations:
            assert agv.current_node is not None
            path = self.graph.shortest_path(agv.current_node, cs.node)
            if path is None:
                continue
            total_dist = sum(
                math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y) for i in range(len(path) - 1)
            )
            # Estimate energy cost to get there
            energy_needed = agv.battery.estimate_energy(total_dist, 0.0, 0.0)
            if agv.battery.level >= energy_needed:
                reachable.append((total_dist, cs))

        if not reachable:
            return None
        return min(reachable, key=lambda x: x[0])[1]

    def _check_pending_queue(self) -> None:
        """Try to dispatch pending orders when AGVs become available."""
        if not self._pending_queue:
            return

        # Try to dispatch each pending order
        dispatched: list[TransferOrder] = []
        failed: list[TransferOrder] = []
        idle_agvs = [agv for agv in self.fleet if agv.state == AGVState.IDLE]
        for order in list(self._pending_queue):
            agv = self._dispatch_strategy.select(order, self.fleet, self.graph)
            if agv is not None:
                dispatched.append(order)
                self._dispatch_retries.pop(order.id, None)
                self._dispatch(order, agv)
            elif idle_agvs:
                self._dispatch_retries[order.id] = self._dispatch_retries.get(order.id, 0) + 1
                if self._dispatch_retries[order.id] >= self._max_dispatch_retries:
                    failed.append(order)

        for order in dispatched:
            self._pending_queue.remove(order)

        for order in failed:
            self._pending_queue.remove(order)
            self._dispatch_retries.pop(order.id, None)
            order.status = OrderStatus.FAILED

        if self._pending_queue:
            self._ensure_pending_retry_loop()

    def _ensure_pending_retry_loop(self) -> None:
        if self._pending_queue and not self._pending_retry_scheduled:
            self._pending_retry_scheduled = True
            self.env.process(self._pending_retry_loop())

    def _pending_retry_loop(self) -> ProcessGenerator:
        while self._pending_queue:
            yield self.env.timeout(self._pending_retry_delay)
            self._check_pending_queue()
        self._pending_retry_scheduled = False

    def _in_transit_orders(self) -> list[TransferOrder]:
        return [
            order
            for order in self._agv_mission.values()
            if order.status not in {OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.FAILED}
        ]

    def _trigger_event_driven_replenishment(self, warehouse: Warehouse) -> None:
        for policy, monitored_wh in self._event_driven_policies:
            if monitored_wh is warehouse:
                new_orders = policy.check(warehouse, self.warehouses, self._in_transit_orders())
                for order in new_orders:
                    self.submit(order)

    def _replenishment_loop(
        self,
        policy: ReplenishmentPolicy,
        warehouse: Warehouse,
        interval: float,
    ) -> ProcessGenerator:
        """Periodic process that checks a replenishment policy and submits orders."""
        while True:
            yield self.env.timeout(interval)
            new_orders = policy.check(warehouse, self.warehouses, self._in_transit_orders())
            for order in new_orders:
                self.submit(order)
