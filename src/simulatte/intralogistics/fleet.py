from __future__ import annotations

import math
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

        # Internal state (keyed by order.id because TransferOrder is unhashable)
        self._active_missions: dict[str, simpy.Process] = {}
        self._agv_mission: dict[AGV, TransferOrder] = {}
        self._pending_queue: list[TransferOrder] = []
        self._low_battery_flags: set[AGV] = set()

        # Lifecycle hook registries
        self._hooks_on_order_submitted: list[Callable[[TransferOrder], None]] = []
        self._hooks_on_order_dispatched: list[Callable[[TransferOrder, AGV], None]] = []
        self._hooks_on_pickup_complete: list[Callable[[TransferOrder, AGV], None]] = []
        self._hooks_on_delivery_complete: list[Callable[[TransferOrder, AGV], None]] = []
        self._hooks_on_battery_low: list[Callable[[AGV], None]] = []
        self._hooks_on_charging_started: list[Callable[[AGV], None]] = []
        self._hooks_on_charging_complete: list[Callable[[AGV], None]] = []
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

    def on_charging_started(self, callback: Callable[[AGV], None]) -> None:
        self._hooks_on_charging_started.append(callback)

    def on_charging_complete(self, callback: Callable[[AGV], None]) -> None:
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
        """Wire a replenishment policy.  If ``check_interval`` is set, spawn
        a periodic SimPy process that checks the policy and submits resulting orders.
        """
        if check_interval is not None:
            self.env.process(self._replenishment_loop(policy, warehouse, check_interval))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _dispatch(self, order: TransferOrder, agv: AGV) -> None:
        """Spawn a mission process for the given order/AGV pair.

        Eagerly sets the order status and AGV state so that subsequent
        ``submit()`` calls in the same simulation step see the AGV as busy.
        """
        order.assigned_agv = agv
        order.status = OrderStatus.DISPATCHED
        order.dispatched_at = self.env.now
        agv.transition_to(AGVState.TRAVELING_EMPTY)

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
                success = yield from self._travel(agv, agv.current_node, origin_output_bay, loaded=False)
                if success:
                    break
                if agv.state == AGVState.STRANDED:
                    order.status = OrderStatus.FAILED
                    return
                # Charging diversion — AGV is at new position, retry travel
                agv.transition_to(AGVState.TRAVELING_EMPTY)

            # 2. Pick
            order.status = OrderStatus.PICKING
            agv.transition_to(AGVState.WAITING_LOAD)
            yield from order.origin.pick(order.sku, order.quantity)
            agv.current_load = {order.sku: order.quantity}
            order.picked_at = self.env.now
            yield self.env.timeout(agv.agv_type.load_time_fn())

            # Fire pickup hooks
            for cb in self._hooks_on_pickup_complete:
                cb(order, agv)
            if self._time_series_collector is not None:
                self._time_series_collector.on_pickup_complete(self, order, agv)

            # 3. Travel loaded to destination input bay
            order.status = OrderStatus.IN_TRANSIT
            agv.transition_to(AGVState.TRAVELING_LOADED)

            dest_input_bay = order.destination.nearest_input_bay(agv.current_node, self.graph)
            while True:
                success = yield from self._travel(agv, agv.current_node, dest_input_bay, loaded=True)
                if success:
                    break
                if agv.state == AGVState.STRANDED:
                    order.status = OrderStatus.FAILED
                    return
                # Charging diversion — AGV is at new position, retry travel
                agv.transition_to(AGVState.TRAVELING_LOADED)

            # 4. Deliver
            order.status = OrderStatus.DELIVERING
            agv.transition_to(AGVState.WAITING_UNLOAD)
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
                    agv.transition_to(AGVState.TRAVELING_EMPTY)
                    yield from self._travel(agv, agv.current_node, target, loaded=False)

            # Go IDLE
            agv.transition_to(AGVState.IDLE)
            for cb in self._hooks_on_agv_idle:
                cb(agv)

        except simpy.Interrupt:
            self.env.debug(
                f"Order {order.id} interrupted (agv={agv.agv_id})",
                component="FleetCoordinator",
            )
            if order.status != OrderStatus.CANCELLED:
                # Not an explicit cancellation — handle gracefully
                if agv.current_load is not None:
                    # Has cargo — delegate to load recovery strategy
                    yield from self._load_recovery_strategy.recover(order, agv, self)
                    agv.current_load = None
                else:
                    # Before pickup — re-queue
                    order.status = OrderStatus.PENDING
                    order.assigned_agv = None
                    self._pending_queue.append(order)
            else:
                # Explicit cancellation — clear the AGV load if any
                if agv.current_load is not None:
                    agv.current_load = None

            agv.transition_to(AGVState.IDLE)
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

        Returns ``True`` on success, ``False`` on failure. When ``False``
        is returned, callers should check ``agv.state``: if
        ``AGVState.STRANDED`` the travel failed permanently; otherwise a
        charging diversion moved the AGV and the caller should retry from
        the AGV's new ``current_node``.
        """
        if from_node == to_node:
            return True

        # H1: return False (with STRANDED) when no path exists
        path = self._path_planner.plan(self.graph, from_node, to_node)
        if path is None:
            self.env.error(
                f"No path from {from_node.id} to {to_node.id} for {agv.agv_id}",
                component="FleetCoordinator",
            )
            agv.transition_to(AGVState.STRANDED)
            return False

        # H2: Check path feasibility with traffic manager. If infeasible,
        # try to re-plan avoiding conflict nodes. If the alternative is
        # also unavailable or infeasible, STRAND — do NOT register and
        # drive the original infeasible path.
        result = self._traffic_manager.check_path(agv, path)
        if not result.feasible and result.conflict_nodes:
            alt_path = self._path_planner.plan(
                self.graph, from_node, to_node, avoid=result.conflict_nodes
            )
            if alt_path is None:
                self.env.error(
                    f"No alternative path from {from_node.id} to {to_node.id} for {agv.agv_id}",
                    component="FleetCoordinator",
                )
                agv.transition_to(AGVState.STRANDED)
                return False
            alt_result = self._traffic_manager.check_path(agv, alt_path)
            if not alt_result.feasible:
                self.env.error(
                    f"Alternative path also infeasible from {from_node.id} to {to_node.id} "
                    f"for {agv.agv_id}",
                    component="FleetCoordinator",
                )
                agv.transition_to(AGVState.STRANDED)
                return False
            path = alt_path

        self._traffic_manager.register_intent(agv, path)

        deadlock_timeout = self._traffic_manager.deadlock_timeout

        try:
            for i in range(len(path) - 1):
                current = path[i]
                next_node = path[i + 1]

                distance = math.hypot(next_node.x - current.x, next_node.y - current.y)

                if loaded and agv.current_load:
                    load_weight = sum(
                        sku.weight * qty for sku, qty in agv.current_load.items()
                    )
                else:
                    load_weight = 0.0

                # M1: Fetch arc speed limit and pass it to speed profile
                arc = self.graph.arc_between(current, next_node)
                arc_speed_limit = arc.speed_limit if arc is not None else None

                travel_time = agv.agv_type.speed_profile.travel_time(
                    distance, load_weight, agv.battery.level_pct, speed_limit=arc_speed_limit
                )
                avg_speed = distance / travel_time if travel_time > 0 else 0.0
                energy_cost = agv.battery._depletion_fn(distance, load_weight, avg_speed)

                # Pre-arc battery check
                if agv.battery.level < energy_cost:
                    # Try to divert to charging
                    charger = self._find_reachable_charger(agv)
                    if charger is not None:
                        prior_state = agv.state
                        yield from self._charge_agv(agv, charger)
                        agv.transition_to(prior_state)
                        # After charging, re-check if we have enough
                        if agv.battery.level < energy_cost:
                            agv.transition_to(AGVState.STRANDED)
                            self.env.error(
                                f"{agv.agv_id} STRANDED at {current.id} — insufficient energy even after charging",
                                component="FleetCoordinator",
                            )
                            return False
                        # H6: Charging diversion moved the AGV — cancel old
                        # intent and return False so _run_mission retries
                        # from the AGV's new current_node.
                        self._traffic_manager.cancel(agv)
                        return False
                    else:
                        agv.transition_to(AGVState.STRANDED)
                        self.env.error(
                            f"{agv.agv_id} STRANDED at {current.id} — no reachable charger",
                            component="FleetCoordinator",
                        )
                        return False

                # S2: Enter next node with deadlock timeout when applicable
                if deadlock_timeout is not None:
                    entered = yield from self._enter_with_timeout(
                        agv, next_node, deadlock_timeout
                    )
                    if not entered:
                        agv.transition_to(AGVState.STRANDED)
                        self.env.error(
                            f"{agv.agv_id} STRANDED — deadlock at node {next_node.id}",
                            component="FleetCoordinator",
                        )
                        return False
                else:
                    yield from self._traffic_manager.enter_node(agv, next_node)

                yield self.env.timeout(travel_time)
                agv.battery.deplete(distance, load_weight, avg_speed)
                self._traffic_manager.leave_node(agv, current)
                agv.current_node = next_node

        finally:
            self._traffic_manager.cancel(agv)

        return True

    def _enter_with_timeout(
        self,
        agv: AGV,
        node: Node,
        timeout: float,
        _max_retries: int = 3,
    ) -> ProcessGenerator:
        """Try to enter ``node`` with a deadlock timeout.

        Retries the *same* node up to ``_max_retries`` times with
        exponential backoff.  Does **not** attempt rerouting — if all
        retries are exhausted the caller (``_travel``) returns ``False``
        so ``_run_mission``'s retry loop can re-plan from scratch.

        Returns ``True`` if the node was entered, ``False`` if all
        attempts were exhausted.
        """
        for attempt in range(_max_retries):
            enter_proc = self.env.process(
                self._traffic_manager.enter_node(agv, node)
            )
            timer = self.env.timeout(timeout)
            yield enter_proc | timer

            if enter_proc.triggered:
                # Successfully entered
                return True

            # Timeout — interrupt the suspended process to prevent leaks,
            # then cancel the pending resource request.
            if enter_proc.is_alive:
                enter_proc.interrupt("deadlock_timeout")
            self._traffic_manager.cancel(agv)

            # Exponential backoff before retrying the same node
            if attempt < _max_retries - 1:
                backoff = timeout * (2 ** attempt)
                yield self.env.timeout(backoff)

        return False

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

        # Fire the constructor-supplied low-battery callback (may be a generator)
        if self._on_low_battery is not None:
            result = self._on_low_battery(agv)
            if result is not None:
                yield from result

        # Travel to charging station
        if agv.current_node != station.node:
            agv.transition_to(AGVState.TRAVELING_EMPTY)
            yield from self._travel(agv, agv.current_node, station.node, loaded=False)

        # Charge
        agv.transition_to(AGVState.CHARGING)
        for cb in self._hooks_on_charging_started:
            cb(agv)

        yield from station.recharge(agv)

        for cb in self._hooks_on_charging_complete:
            cb(agv)

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
            return sum(
                math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y)
                for i in range(len(path) - 1)
            )

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
                math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y)
                for i in range(len(path) - 1)
            )
            # Estimate energy cost to get there
            energy_needed = agv.battery._depletion_fn(total_dist, 0.0, 0.0)
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
        for order in list(self._pending_queue):
            agv = self._dispatch_strategy.select(order, self.fleet, self.graph)
            if agv is not None:
                dispatched.append(order)
                self._dispatch(order, agv)

        for order in dispatched:
            self._pending_queue.remove(order)

    def _replenishment_loop(
        self,
        policy: ReplenishmentPolicy,
        warehouse: Warehouse,
        interval: float,
    ) -> ProcessGenerator:
        """Periodic process that checks a replenishment policy and submits orders."""
        while True:
            yield self.env.timeout(interval)
            in_transit = [
                order
                for order in self._agv_mission.values()
                if order.status not in {OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.FAILED}
            ]
            new_orders = policy.check(warehouse, self.warehouses, in_transit)
            for order in new_orders:
                self.submit(order)
