from __future__ import annotations

import math
import sys
from collections import deque
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict, cast

import simpy

from simulatte._wire import FrozenMap, freeze, wire_float
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import deferrable
from simulatte.events import Deltas
from simulatte.layout.binding import graph_input, pending, readonly
from simulatte.intralogistics.agv import AGVState
from simulatte.intralogistics.events import (
    AgvLoadChanged,
    AgvStranded,
    FleetAgvAdded,
    FleetPendingChanged,
    OrderAssigned,
    OrderStatusChanged,
    OrderUnassigned,
)
from simulatte.intralogistics.metrics import OrderEMACollector
from simulatte.intralogistics.order import TERMINAL_STATUSES, OrderStatus, TransferOrder
from simulatte.intralogistics.policies import (
    NearestIdleStrategy,
    RepositioningContext,
    ReturnToOrigin,
    RoundRobinStrategy,
    StayInPlace,
)
from simulatte.intralogistics.routing import RouteRequest, ShortestDistanceRouter
from simulatte.intralogistics.traffic import FreeFlowController
from simulatte.intralogistics.travel import TravelExecutor, TravelOutcome, standard_target

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Unpack

    from simpy.events import ProcessGenerator

    from simulatte.environment import Environment
    from simulatte.layout.refs import GraphRef
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.charging import ChargingStation
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.parking import ParkingArea
    from simulatte.intralogistics.policies import (
        DispatchStrategy,
        LoadRecoveryStrategy,
        ReplenishmentPolicy,
        RepositioningPolicy,
    )
    from simulatte.intralogistics.routing import Router
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.traffic import TrafficController
    from simulatte.intralogistics.warehouse import Warehouse


class _TransferOrderOptions(TypedDict, total=False):
    """Optional ``TransferOrder`` fields accepted by ``FleetCoordinator.create_order``."""

    due_date: float | None
    priority: float
    status: OrderStatus
    dispatched_at: float | None
    picked_at: float | None
    delivered_at: float | None
    assigned_agv: AGV | None


_TRAVEL_FAILED = frozenset({TravelOutcome.NO_ROUTE, TravelOutcome.GAVE_UP})
"""Travel outcomes that fail the mission as ``travel_failed``. ``GAVE_UP`` cannot occur yet (no travel accepts a
give-up); until it gets its own handling (spec §8.2) it is treated like ``NO_ROUTE``."""


_STATUS_TIMESTAMPS = {OrderStatus.DISPATCHED: "dispatched_at", OrderStatus.COMPLETED: "delivered_at"}
"""The order timestamp set together with each status (spec §6.4, ``order.status_changed``)."""


class FleetCoordinator(Entity, kind="fleet"):
    """Central orchestrator for AGV fleet operations and mission lifecycle.

    Manages transfer orders from submission through dispatch, travel, pick,
    transit, deliver, and completion.  Analogous to ``ShopFloor`` for
    production simulations but focused on warehouse-to-warehouse AGV transport.

    Idle AGVs are kept out of the way (spec §7.10): :meth:`make_way` moves an idle AGV that blocks a claim, and with
    ``clear_service_nodes`` (default true) an AGV that would become idle on a service node (a bay of a warehouse or the
    node of a charging station) first moves off it, when the traffic controller controls occupancy. A move to the node
    of a parking area takes a slot of the area (``area.enter``) at the decision and gives it back (``area.leave``) when
    the AGV next departs from that node or when the move does not arrive.

    Its id is ``name`` when given, otherwise ``fleet-<n>``. Construction binds the graph's nodes (sorted by id)
    in the environment, registers the fleet with its traffic controller (``traffic_controller``, default
    :class:`~simulatte.intralogistics.traffic.FreeFlowController`) and registers an activation initializer that places
    the AGVs on their starting nodes with it (``place``). Travels run through a
    :class:`~simulatte.intralogistics.travel.TravelExecutor`, on routes of ``router`` (default
    :class:`~simulatte.intralogistics.routing.ShortestDistanceRouter`). Orders are attached by :meth:`create_order`
    (or by :meth:`submit` for orders constructed directly) and retire at a terminal status, once the hooks of that
    transition ran and the mission bookkeeping was cleaned up. :meth:`submit` and :meth:`cancel` are deferrable: before
    activation they are queued and run at activation (spec §10).

    Unless built with ``default_metrics=False``, it attaches an :class:`~simulatte.intralogistics.OrderEMACollector`
    as ``metrics`` (with smoothing factor ``ema_alpha``, default 0.01); other collectors
    (:class:`~simulatte.intralogistics.FleetTimeSeries`,
    :class:`~simulatte.intralogistics.FleetKPIs`) are attached with ``collector.attach(env)``.

    Events (spec §6.4): ``fleet.agv_added`` per AGV at construction, ``fleet.pending_changed`` at every change of
    the pending queue, ``order.status_changed`` at every assignment of an order status, ``order.assigned`` /
    ``order.unassigned`` for the order-AGV link, and the AGV events of movement (``agv.move_started``,
    ``agv.move_ended``, ``agv.move_interrupted``, ``agv.routed``, emitted by the travel executor), cargo
    (``agv.load_changed``) and stranding (``agv.stranded``).
    """

    state_schema: ClassVar[StateSchema] = StateSchema({"pending": FieldSpec("str", collection="list")})

    def __init__(
        self,
        *,
        env: Environment,
        graph: LayoutGraph | GraphRef,
        fleet: list[AGV],
        warehouses: list[Warehouse],
        charging_stations: list[ChargingStation],
        parking_areas: list[ParkingArea] | None = None,
        traffic_controller: TrafficController | None = None,
        router: Router | None = None,
        dispatch_strategy: DispatchStrategy | None = None,
        repositioning_policy: RepositioningPolicy | None = None,
        load_recovery_strategy: LoadRecoveryStrategy | None = None,
        clear_service_nodes: bool = True,
        default_metrics: bool = True,
        ema_alpha: float = 0.01,
        on_low_battery: Callable[[AGV], ProcessGenerator | None] | None = None,
        max_dispatch_retries: int = 10,
        pending_retry_delay: float = 1.0,
        name: str | None = None,
        label: str | None = None,
    ) -> None:
        self.env = env
        self._graph, self._pending_graph = graph_input(env, graph)
        self.fleet = tuple(fleet)
        self.warehouses = tuple(warehouses)
        self.charging_stations = tuple(charging_stations)
        self.parking_areas = tuple(parking_areas or [])

        self.traffic_controller: TrafficController = (
            FreeFlowController() if traffic_controller is None else traffic_controller
        )
        """The traffic controller that grants the claims of this fleet's travels (spec §7)."""
        self.router: Router = ShortestDistanceRouter() if router is None else router
        """The router of this fleet's travels (spec §10)."""
        if env.layout is not None:
            env.layout._validate_fleet(
                (*self.fleet, *self.warehouses, *self.charging_stations, *self.parking_areas), self.traffic_controller
            )
            if any(agv.fleet is not None for agv in self.fleet) or len(set(self.fleet)) != len(self.fleet):
                from simulatte.layout.errors import LayoutError

                raise LayoutError("AGV already belongs to a fleet or occurs twice")
        self.traffic_controller.register_fleet(self)
        self._executor = TravelExecutor(self)
        self.clear_service_nodes = clear_service_nodes
        """Whether an AGV that would become idle on a service node first moves off it (spec §7.10)."""
        self._returned: dict[AGV, tuple[float, Node]] = {}  # make-way moves that ended where they started: time, node
        self._slots: dict[AGV, list[ParkingArea]] = {}  # the parking slots taken at a decision or held at rest
        self._dispatch_strategy: DispatchStrategy = dispatch_strategy or NearestIdleStrategy()
        self._repositioning_policy: RepositioningPolicy = repositioning_policy or StayInPlace()
        self._load_recovery_strategy: LoadRecoveryStrategy = load_recovery_strategy or ReturnToOrigin()
        self._on_low_battery = on_low_battery
        self._max_dispatch_retries = max_dispatch_retries
        self._dispatch_retries: dict[str, int] = {}
        self._pending_retry_scheduled = False
        self._pending_retry_delay = pending_retry_delay

        # Event-driven replenishment policies (checked after each pick)
        self._event_driven_policies: list[tuple[ReplenishmentPolicy, Warehouse]] = []

        # Internal state (keyed by order.id because TransferOrder is unhashable)
        self._active_missions: dict[str, simpy.Process] = {}
        self._cancelled_missions: set[str] = set()
        self._agv_mission: dict[AGV, TransferOrder] = {}
        self._pending_queue: deque[TransferOrder] = deque()
        self._pending_unserviceable: dict[str, TransferOrder] = {}
        self._low_battery_flags: set[AGV] = set()
        self._dropped_cargo: list[tuple[float, Node, SKU, int]] = []
        self._hooks_on_cargo_dropped: list[Callable[[AGV, Node, SKU, int], None]] = []

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

        self._uses_explicit_graph = env.layout is None and True
        env.entities.attach(self, name=name, label=label)
        self.metrics: OrderEMACollector | None = (
            OrderEMACollector(self, alpha=ema_alpha).attach(env) if default_metrics else None
        )
        """The default :class:`OrderEMACollector` (``ema_*`` averages of delivered orders), or None when built with
        ``default_metrics=False``."""
        if env.layout is None:
            for node in sorted(self.graph.nodes, key=lambda node: node.id):
                env.entities.bind_node(node)
        for agv in self.fleet:
            agv.fleet = self
            if env.wants(FleetAgvAdded):
                env.emit(
                    FleetAgvAdded(fleet=self.id, agv=agv.id, deltas=Deltas.build().set(agv.id, "fleet", self.id).done())
                )

        # S1: Initial AGV placement — register starting positions with the traffic controller at activation
        env.on_activate(self._initial_placement)

    @property
    def graph(self) -> LayoutGraph:
        if self._pending_graph is not None:
            pending(self, "graph")
        assert self._graph is not None
        return self._graph

    @graph.setter
    def graph(self, value: LayoutGraph) -> None:
        readonly(self.env, "graph")
        self._graph = value

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: the ids of the pending orders, in queue order."""
        return {"pending": [order.id for order in self._pending_queue], "label": self.label}

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
        **kwargs: Unpack[_TransferOrderOptions],
    ) -> TransferOrder:
        """Factory method that creates a ``TransferOrder`` with ``created_at`` set to now and attaches it.

        The order is attached immediately, before activation too (ruling R18), so it has its id at once.
        """
        order = TransferOrder(
            sku=sku,
            quantity=quantity,
            origin=origin,
            destination=destination,
            created_at=self.env.now,
            **kwargs,
        )
        self._attach_order(order)
        return order

    def _attach_order(self, order: TransferOrder) -> None:
        """Attach `order` with this fleet as its owner, unless it already has an id."""
        if order.id is None:
            order.fleet_id = self.id
            self.env.entities.attach(order)

    def submit(self, order: TransferOrder) -> None:
        """Submit an order for dispatch.

        If an idle AGV is available, the mission is spawned immediately.
        Otherwise the order enters ``_pending_queue``. An order constructed directly is attached first.

        Deferrable: before activation the submission is queued and the order reports
        ``OrderStatus.PENDING_ACTIVATION`` until it runs at activation.
        """
        self._attach_order(order)
        if not self.env.activated:
            self._set_status(order, OrderStatus.PENDING_ACTIVATION, "awaiting_activation")
        self._submit(order)

    @deferrable
    def _submit(self, order: TransferOrder) -> None:
        # Fire hooks
        for cb in self._hooks_on_order_submitted:
            cb(order)

        agv = self._dispatch_strategy.select(order, self.fleet, self.graph)
        if agv is not None:
            self._dispatch(order, agv)
        else:
            self._set_status(order, OrderStatus.PENDING, "no_idle_agv")
            self._pending_add(order)
            self._ensure_pending_retry_loop()

    @deferrable
    def cancel(self, order: TransferOrder) -> None:
        """Cancel an active or pending order; terminal orders are unchanged.

        Deferrable: before activation the cancellation is queued and runs at activation, after the commands
        queued before it.
        """
        if order.status in TERMINAL_STATUSES:
            return

        # If pending, just remove from queue
        if order in self._pending_queue:
            self._pending_remove(order)
            self._set_status(order, OrderStatus.CANCELLED, "cancelled")
            self._retire_if_terminal(order)
            return

        # If active, interrupt the mission process
        process = self._active_missions.get(order.id)
        if process is not None and process.is_alive:
            self._cancelled_missions.add(order.id)
            process.interrupt("cancelled")
        self._set_status(order, OrderStatus.CANCELLED, "cancelled")
        if process is None:
            self._retire_if_terminal(order)  # otherwise the mission retires it after its cleanup

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

    def on_cargo_dropped(self, callback: Callable[[AGV, Node, SKU, int], None]) -> None:
        self._hooks_on_cargo_dropped.append(callback)

    # ------------------------------------------------------------------
    # Fleet convenience
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Number of orders waiting in the pending queue."""
        return len(self._pending_queue)

    @property
    def fleet_utilization(self) -> float:
        """Average utilization across the fleet."""
        if not self.fleet:
            return 0.0
        return math.fsum(agv.utilization() for agv in self.fleet) / len(self.fleet)

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
        """Transition an AGV to *new_state* (``AGV.transition_to`` emits ``agv.state_changed``)."""
        agv.transition_to(new_state)

    def _set_status(self, order: TransferOrder, status: OrderStatus, reason: str) -> None:
        """Assign `status` to `order` and emit ``order.status_changed``."""
        previous = order.status
        order.status = status
        self._status_changed(order, previous, reason)

    def _status_changed(
        self, order: TransferOrder, previous: OrderStatus, reason: str, *, with_agv: bool = False
    ) -> None:
        """Emit ``order.status_changed`` for the status `order` already holds, if the order is live.

        The deltas set the status and the timestamp that goes with it (``dispatched_at``, ``delivered_at``), and the
        order's ``agv`` when `with_agv` (a load-recovery strategy changed both). A status assigned to a retired
        order (cancelling it again) changes no live entity and emits nothing.
        """
        env = self.env
        if env.wants(OrderStatusChanged) and env.entities.is_live(order):
            status = order.status
            build = Deltas.build().set(order.id, "status", status.name)
            field = _STATUS_TIMESTAMPS.get(status)
            if field is not None:
                value = getattr(order, field)
                build.set(order.id, field, None if value is None else wire_float(value))
            if with_agv:
                agv = order.assigned_agv
                build.set(order.id, "agv", None if agv is None else agv.id)
            env.emit(
                OrderStatusChanged(
                    order=order.id, status=status.name, previous=previous.name, reason=reason, deltas=build.done()
                )
            )

    def _pending_add(self, order: TransferOrder) -> None:
        """Append `order` to the pending queue and emit ``fleet.pending_changed``."""
        queue = self._pending_queue
        # Compatibility predicates are user code: call them before changing the
        # queue so any events they emit still see the last published state.
        if type(self._dispatch_strategy) in (NearestIdleStrategy, RoundRobinStrategy):
            if not any(agv.can_carry(order.sku, order.quantity) for agv in self.fleet):
                self._pending_unserviceable[order.id] = order
        queue.append(order)
        env = self.env
        if env.wants(FleetPendingChanged):
            index = len(queue) - 1
            env.emit(
                FleetPendingChanged(
                    fleet=self.id,
                    order=order.id,
                    op="added",
                    index=index,
                    deltas=Deltas.build().insert(self.id, "pending", index, order.id).done(),
                )
            )

    def _pending_remove(self, order: TransferOrder) -> None:
        """Remove `order` from the pending queue (as ``list.remove`` does) and emit ``fleet.pending_changed``."""
        queue = self._pending_queue
        index = queue.index(order)
        del queue[index]
        self._pending_unserviceable.pop(order.id, None)
        self._dispatch_retries.pop(order.id, None)
        env = self.env
        if env.wants(FleetPendingChanged):
            env.emit(
                FleetPendingChanged(
                    fleet=self.id,
                    order=order.id,
                    op="removed",
                    index=index,
                    deltas=Deltas.build().remove(self.id, "pending", order.id).done(),
                )
            )

    def _set_load(self, agv: AGV, load: dict[SKU, int] | None, *, picked: TransferOrder | None = None) -> None:
        """Assign the AGV's cargo and emit ``agv.load_changed``.

        At pickup (`picked`) the order's ``picked_at`` is stamped too, and the event carries it.
        """
        agv.current_load = load
        env = self.env
        if picked is not None:
            picked.picked_at = env.now
        if env.wants(AgvLoadChanged):
            wire = None if load is None else cast("FrozenMap", freeze({sku.id: qty for sku, qty in load.items()}))
            build = Deltas.build().set(agv.id, "load", wire)
            if picked is not None:
                build.set(picked.id, "picked_at", wire_float(env.now))
            env.emit(AgvLoadChanged(agv=agv.id, load=wire, deltas=build.done()))

    def _unassign_order(self, order: TransferOrder, agv: AGV) -> None:
        """Clear the order's ``agv`` (re-queue after an interruption) and emit ``order.unassigned``."""
        order.assigned_agv = None
        env = self.env
        if env.wants(OrderUnassigned):
            env.emit(
                OrderUnassigned(order=order.id, agv=agv.id, deltas=Deltas.build().set(order.id, "agv", None).done())
            )

    def _after_load_recovery(self, order: TransferOrder, agv: AGV, status: OrderStatus, assigned: AGV | None) -> None:
        """Emit the order changes the load-recovery strategy made: its status, its ``agv``.

        Strategies are user code that assigns the order's fields directly; `status` and `assigned` are the values
        before the strategy ran, and only changes are seen. Both changes happened before any event, so a status
        change carries the ``agv`` change in its deltas (``ReturnToOrigin`` sets ``PENDING`` and clears the AGV);
        an ``agv`` change alone emits ``order.unassigned`` or ``order.assigned``.
        """
        current = order.assigned_agv
        if order.status is not status:
            self._status_changed(order, status, "load_recovery", with_agv=current is not assigned)
            return
        if current is assigned:
            return
        env = self.env
        if current is None:
            if env.wants(OrderUnassigned):
                env.emit(
                    OrderUnassigned(order=order.id, agv=agv.id, deltas=Deltas.build().set(order.id, "agv", None).done())
                )
        elif env.wants(OrderAssigned):
            env.emit(
                OrderAssigned(
                    order=order.id, agv=current.id, deltas=Deltas.build().set(order.id, "agv", current.id).done()
                )
            )

    def _retire_if_terminal(self, order: TransferOrder) -> None:
        """Retire `order` if its status is terminal and it is still live (spec §5.1)."""
        if order.status in TERMINAL_STATUSES and self.env.entities.is_live(order):
            self.env.entities.retire(order)

    def _dispatch(self, order: TransferOrder, agv: AGV) -> None:
        """Spawn a mission process for the given order/AGV pair.

        Eagerly sets the order status and AGV state so that subsequent
        ``submit()`` calls in the same simulation step see the AGV as busy.
        """
        env = self.env
        order.dispatched_at = env.now
        self._set_status(order, OrderStatus.DISPATCHED, "dispatched")
        order.assigned_agv = agv
        agv.order = order
        if env.wants(OrderAssigned):
            env.emit(
                OrderAssigned(
                    order=order.id,
                    agv=agv.id,
                    deltas=Deltas.build().set(order.id, "agv", agv.id).set(agv.id, "order", order.id).done(),
                )
            )
        self._transition_agv(agv, AGVState.TRAVELING_EMPTY)

        process = self.env.process(self._run_mission(order, agv))
        self._active_missions[order.id] = process
        self._agv_mission[agv] = order

        # Fire hooks
        for cb in self._hooks_on_order_dispatched:
            cb(order, agv)

    def _require_current_node(self, agv: AGV) -> Node:
        """Return the AGV's current node, or fail on a broken mission invariant."""
        if agv.current_node is None:
            raise RuntimeError(f"{agv.agv_id} has no current node")
        return agv.current_node

    def _run_mission(self, order: TransferOrder, agv: AGV) -> ProcessGenerator:
        """Full mission lifecycle as a SimPy process."""
        mission = self.env.active_process
        notify_idle = False
        try:
            # 1. Travel empty to origin output bay
            # (order.status, dispatched_at, and AGV state are set eagerly in _dispatch)
            origin_output_bay = order.origin.nearest_output_bay(self._require_current_node(agv), self.graph)
            while True:
                outcome = yield from self._executor.travel(agv, origin_output_bay, loaded=False)
                if outcome is TravelOutcome.ARRIVED:
                    break
                if outcome is TravelOutcome.BATTERY_STRANDED:
                    self._set_status(order, OrderStatus.FAILED, "battery_stranded")
                    return
                if outcome in _TRAVEL_FAILED:
                    self._set_status(order, OrderStatus.FAILED, "travel_failed")
                    self._transition_agv(agv, AGVState.IDLE)
                    return
                # Charging diversion or critical battery — charge first if needed
                if agv.battery.is_critical and self.charging_stations:
                    yield from self._charge_agv(agv)
                self._transition_agv(agv, AGVState.TRAVELING_EMPTY)

            # 2. Pick
            self._set_status(order, OrderStatus.PICKING, "arrived_at_origin")
            self._transition_agv(agv, AGVState.WAITING_LOAD)

            def _mark_pick_committed() -> None:
                self._committed_picks[order.id] = (order.origin, order.sku, order.quantity)

            yield from order.origin.pick(order.sku, order.quantity, on_committed=_mark_pick_committed)
            del self._committed_picks[order.id]
            self._set_load(agv, {order.sku: order.quantity}, picked=order)
            yield self.env.timeout(agv.sample_load_time())

            # Fire pickup hooks
            for cb in self._hooks_on_pickup_complete:
                cb(order, agv)

            # 3. Travel loaded to destination input bay
            self._set_status(order, OrderStatus.IN_TRANSIT, "picked")
            self._trigger_event_driven_replenishment(order.origin)
            self._transition_agv(agv, AGVState.TRAVELING_LOADED)

            dest_input_bay = order.destination.nearest_input_bay(self._require_current_node(agv), self.graph)
            while True:
                outcome = yield from self._executor.travel(agv, dest_input_bay, loaded=True)
                if outcome is TravelOutcome.ARRIVED:
                    break
                if outcome is TravelOutcome.BATTERY_STRANDED:
                    if agv.current_load is not None:
                        yield from self._return_cargo_to_origin(order, agv)
                    self._set_status(order, OrderStatus.FAILED, "battery_stranded")
                    self._transition_agv(agv, AGVState.STRANDED)
                    return
                if outcome in _TRAVEL_FAILED:
                    if agv.current_load is not None:
                        yield from self._return_cargo_to_origin(order, agv)
                    self._set_status(order, OrderStatus.FAILED, "travel_failed")
                    self._transition_agv(agv, AGVState.IDLE)
                    return
                # Charging diversion or critical battery — charge first if needed
                if agv.battery.is_critical and self.charging_stations:
                    yield from self._charge_agv(agv)
                self._transition_agv(agv, AGVState.TRAVELING_LOADED)

            # 4. Deliver
            self._set_status(order, OrderStatus.DELIVERING, "arrived_at_destination")
            self._transition_agv(agv, AGVState.WAITING_UNLOAD)
            yield from order.destination.put(order.sku, order.quantity)
            self._set_load(agv, None)
            yield self.env.timeout(agv.sample_unload_time())
            order.delivered_at = self.env.now
            self._set_status(order, OrderStatus.COMPLETED, "delivered")

            # 5. Post-mission
            for cb in self._hooks_on_delivery_complete:
                cb(order, agv)

            # Battery check after mission
            target = None
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
                    if self._take_slot(agv, target):
                        self._transition_agv(agv, AGVState.TRAVELING_EMPTY)
                        outcome = yield from self._idle_travel(agv, target)
                        if outcome is TravelOutcome.BATTERY_STRANDED:
                            return
                        if outcome in _TRAVEL_FAILED:
                            self.env.warning(
                                f"Repositioning failed for {agv.agv_id} — no path to {target.id}",
                                component="FleetCoordinator",
                            )
                    else:  # a full parking area is not a valid target (spec §7.10)
                        target = None

            # Clear a service node the AGV would otherwise idle on
            if target is None and (yield from self._clear_service_node(agv)) is TravelOutcome.BATTERY_STRANDED:
                return

            # Go IDLE
            self._transition_agv(agv, AGVState.IDLE)
            notify_idle = True

        except simpy.Interrupt:
            # Recovery owns cargo/inventory until it finishes. Further interrupts
            # are merged here rather than injected into a half-finished rollback.
            recovery = self.env.process(self._recover_mission(order, agv))
            while True:
                try:
                    yield recovery
                    break
                except simpy.Interrupt:
                    if recovery.triggered and not recovery.ok:
                        raise  # a failure inside recovery is not a new interrupt of this mission
                    continue
            if order.id in self._cancelled_missions and order.status != OrderStatus.CANCELLED:
                self._set_status(order, OrderStatus.CANCELLED, "cancelled")
            self._transition_agv(agv, AGVState.IDLE)
            notify_idle = True

        finally:
            # Cleanup mission tracking
            self._cancelled_missions.discard(order.id)
            if self._active_missions.get(order.id) is mission:
                self._active_missions.pop(order.id)
            if self._agv_mission.get(agv) is order:
                self._agv_mission.pop(agv)
            if agv.order is order:
                agv.order = None
                env = self.env
                if env.wants(OrderUnassigned):
                    env.emit(
                        OrderUnassigned(
                            order=order.id, agv=agv.id, deltas=Deltas.build().set(agv.id, "order", None).done()
                        )
                    )
            # A generator closed unfinished (the process is discarded, e.g. garbage-collected) retires nothing.
            closed = isinstance(sys.exc_info()[1], GeneratorExit)
            if not closed:
                self._retire_if_terminal(order)

            # Hooks may dispatch immediately, so all old mission ownership must
            # be gone before the AGV is offered to user code.
            if notify_idle:
                for cb in self._hooks_on_agv_idle:
                    cb(agv)
            self._check_pending_queue()
            if not closed:
                self._notify_idle(agv)

    def _recover_mission(self, order: TransferOrder, agv: AGV) -> ProcessGenerator:
        """Finish an interrupted mission without exposing rollback to more interrupts."""
        # H5: Roll back committed but unloaded pick (inventory deducted
        # inside warehouse.pick() but not yet assigned to agv.current_load).
        committed = self._committed_picks.pop(order.id, None)
        if committed is not None:
            wh, sku, qty = committed
            yield from wh.put(sku, qty)

        if order.status in TERMINAL_STATUSES and order.status != OrderStatus.CANCELLED:
            return

        if order.status != OrderStatus.CANCELLED:
            # Not an explicit cancellation — handle gracefully
            if agv.current_load is not None:
                # Has cargo — delegate to load recovery strategy for intent
                status_before, assigned_before = order.status, order.assigned_agv
                yield from self._load_recovery_strategy.recover(order, agv, self)
                self._after_load_recovery(order, agv, status_before, assigned_before)
                if order.id in self._cancelled_missions and order.status != OrderStatus.CANCELLED:
                    self._set_status(order, OrderStatus.CANCELLED, "cancelled")

                if order.status == OrderStatus.IN_TRANSIT and agv.current_load is not None:
                    # S6: ResumeDelivery — re-travel to destination from current position
                    dest_input_bay = order.destination.nearest_input_bay(self._require_current_node(agv), self.graph)
                    self._transition_agv(agv, AGVState.TRAVELING_LOADED)
                    while True:
                        outcome = yield from self._executor.travel(agv, dest_input_bay, loaded=True)
                        if outcome is TravelOutcome.ARRIVED:
                            break
                        if outcome is TravelOutcome.BATTERY_STRANDED or outcome in _TRAVEL_FAILED:
                            # H1 fix: fall back to return-to-origin, then drop
                            yield from self._return_cargo_to_origin(order, agv)
                            stranded = outcome is TravelOutcome.BATTERY_STRANDED
                            reason = "battery_stranded" if stranded else "travel_failed"
                            self._set_status(order, OrderStatus.FAILED, reason)
                            break
                        if agv.battery.is_critical and self.charging_stations:
                            yield from self._charge_agv(agv)
                        self._transition_agv(agv, AGVState.TRAVELING_LOADED)

                    if order.status == OrderStatus.CANCELLED:
                        yield from self._return_cargo_to_origin(order, agv)
                    elif order.status == OrderStatus.IN_TRANSIT:
                        # Successfully re-traveled — complete delivery
                        self._set_status(order, OrderStatus.DELIVERING, "arrived_at_destination")
                        self._transition_agv(agv, AGVState.WAITING_UNLOAD)
                        yield from order.destination.put(order.sku, order.quantity)
                        self._set_load(agv, None)
                        yield self.env.timeout(agv.sample_unload_time())
                        if order.id in self._cancelled_missions:
                            return
                        order.delivered_at = self.env.now
                        self._set_status(order, OrderStatus.COMPLETED, "delivered")

                        for cb in self._hooks_on_delivery_complete:
                            cb(order, agv)
                elif agv.current_load is not None:
                    # ReturnToOrigin (or similar) — physically return cargo
                    yield from self._return_cargo_to_origin(order, agv)
                    if order.status == OrderStatus.PENDING:
                        self._pending_add(order)
                        self._ensure_pending_retry_loop()
            else:
                # Before pickup — re-queue
                self._set_status(order, OrderStatus.PENDING, "interrupted")
                self._unassign_order(order, agv)
                self._pending_add(order)
                self._ensure_pending_retry_loop()
        else:
            # Explicit cancellation — physically return cargo to origin
            if agv.current_load is not None:
                yield from self._return_cargo_to_origin(order, agv)
            # Ensure status stays CANCELLED (may have been changed by _return_cargo_to_origin)
            self._set_status(order, OrderStatus.CANCELLED, "cancelled")

    def _stranded(self, agv: AGV, node: Node, reason: str) -> None:
        env = self.env
        if env.wants(AgvStranded):
            env.emit(AgvStranded(agv=agv.id, node=node.id, reason=reason))

    @property
    def service_nodes(self) -> frozenset[Node]:
        """The nodes AGVs should not idle on: the bays of the fleet's warehouses and the nodes of its charging
        stations (spec §7.10)."""
        nodes = {station.node for station in self.charging_stations}
        for warehouse in self.warehouses:
            nodes.update(warehouse.input_bays)
            nodes.update(warehouse.output_bays)
        return frozenset(nodes)

    def make_way(self, agv: AGV, target: Node | None = None, *, concerned: Sequence[AGV] = ()) -> bool:
        """Move the idle `agv` out of the way of the waiting AGVs `concerned`, to `target` or, without one, to the node
        of the standard search for an idle AGV (spec §7.9, §7.10); whether a move was started.

        Nothing happens, and false is returned, for an AGV that is not idle (one already making way is not), when there
        is no target, when the target is the AGV's own node, a node the router cannot reach from the AGV's node
        (asked with purpose ``make_way``, for a target that was given) or a parking area without a free slot, and when
        the AGV's previous make-way move ended at the current time on the node it started from (so that a move that
        fails at once is not repeated without time passing). Otherwise the
        AGV leaves ``IDLE`` at once (``TRAVELING_EMPTY``), a travel with purpose ``make_way`` runs as a process, and
        when it ends, whatever the outcome (a stranded AGV stays stranded), the AGV is ``IDLE`` again and the
        ``on_agv_idle`` hooks and the pending-queue check run as after any mission.
        """
        origin = agv.current_node
        if agv.state is not AGVState.IDLE or origin is None:
            return False
        if self._returned.get(agv) == (float(self.env.now), origin):
            return False
        if target is None:
            target = standard_target(self, agv, concerned=concerned, idle=True, purpose="make_way")
        elif target != origin and not self._reachable(agv, origin, target):
            return False
        if target is None or target == origin or not self._take_slot(agv, target):
            return False
        self._transition_agv(agv, AGVState.TRAVELING_EMPTY)
        self.env.process(self._make_way_move(agv, target))
        return True

    def _reachable(self, agv: AGV, origin: Node, target: Node) -> bool:
        """Whether the router finds a route for a make-way move of `agv` from `origin` to `target`."""
        request = RouteRequest(agv, origin, target, 0.0, purpose="make_way")
        return self.router.route(request, self.traffic_controller.view) is not None

    def _make_way_move(self, agv: AGV, target: Node) -> ProcessGenerator:
        """The process of one make-way move: the travel, then the idle bookkeeping of a finished mission."""
        origin = self._require_current_node(agv)
        outcome = yield from self._idle_travel(agv, target, "make_way")
        if outcome is TravelOutcome.BATTERY_STRANDED:
            return
        if agv.current_node == origin:
            self._returned[agv] = (float(self.env.now), origin)
        self._transition_agv(agv, AGVState.IDLE)
        for cb in self._hooks_on_agv_idle:
            cb(agv)
        self._check_pending_queue()
        self._notify_idle(agv)

    def _notify_idle(self, agv: AGV) -> None:
        """Tell the traffic controller that `agv` became idle where it stands, unless the hooks or the dispatch of a
        pending order already took it out of ``IDLE`` (spec §7.1, §7.10)."""
        if agv.state is AGVState.IDLE:
            self.traffic_controller.idle(agv)

    def _clear_service_node(self, agv: AGV) -> ProcessGenerator:
        """Move `agv` off the service node it would idle on, to the standard-search node (spec §7.10).

        Only with ``clear_service_nodes``, a traffic controller that controls occupancy and an empty pending queue (the
        AGV is about to be dispatched where it stands). Returns the outcome of the travel, or None when the AGV stays
        (not on a service node, or no target).
        """
        if not self.clear_service_nodes or not self.traffic_controller.controls_occupancy or self._pending_queue:
            return None
        if agv.current_node not in self.service_nodes:
            return None
        target = standard_target(self, agv, idle=True, purpose="clear_node")
        if target is None or not self._take_slot(agv, target):
            return None
        self._transition_agv(agv, AGVState.TRAVELING_EMPTY)
        return (yield from self._idle_travel(agv, target, "clear_node"))

    def _idle_travel(self, agv: AGV, target: Node, purpose: str = "travel") -> ProcessGenerator:
        """Travel `agv` unloaded to `target` and return the outcome. The parking slot that :meth:`_take_slot` took for
        `target` is given back, whatever the outcome and for an interrupt, unless the AGV stands on `target` (an
        interrupt takes effect at the end of the arc, which may be the last one)."""
        try:
            outcome = yield from self._executor.travel(agv, target, loaded=False, purpose=purpose)
        except simpy.Interrupt:
            self._release_slots_unless_there(agv, target)
            raise
        if outcome is not TravelOutcome.ARRIVED:
            self._release_slots_unless_there(agv, target)
        return outcome

    # Parking slots (spec §7.10)

    def _take_slot(self, agv: AGV, target: Node) -> bool:
        """Take a slot for `agv` in the first parking area at `target` that has one, at once, and return true.

        True without a slot when `target` is not the node of a parking area; false when it is and every area there is
        full (nothing is taken).
        """
        areas = [area for area in self.parking_areas if area.node == target]
        if not areas:
            return True
        for area in areas:
            if area.available_capacity > 0:
                for _ in area.enter(agv):  # the request is granted already: the process yields it once and ends
                    pass
                self._slots.setdefault(agv, []).append(area)
                return True
        return False

    def _release_slots_unless_there(self, agv: AGV, node: Node) -> None:
        """Give back the slots `agv` holds at `node` unless it stands on `node`."""
        if agv.current_node != node:
            self._release_slots(agv, node)

    def _release_slots(self, agv: AGV, node: Node) -> None:
        """Give back the slots `agv` holds in the parking areas at `node`."""
        areas = self._slots.get(agv)
        if areas is None:
            return
        for area in [area for area in areas if area.node == node]:
            areas.remove(area)
            area.leave(agv)
        if not areas:
            del self._slots[agv]

    def _initial_placement(self) -> None:
        """Activation initializer: place every AGV that has a node with the traffic controller (S1).

        The controller raises `RuntimeError` when a starting node cannot be held.
        """
        for agv in self.fleet:
            if agv.current_node is not None:
                self.traffic_controller.place(agv, agv.current_node)

    def _charge_agv(self, agv: AGV, station: ChargingStation | None = None) -> ProcessGenerator:
        """Navigate to a charging station and recharge."""
        if station is None:
            station = self._find_reachable_charger(agv) or self._find_nearest_charger(agv)
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
        current_node = agv.current_node
        if current_node != station.node:
            self._transition_agv(agv, AGVState.TRAVELING_EMPTY)
            outcome = yield from self._executor.travel(agv, station.node, loaded=False)
            if outcome is not TravelOutcome.ARRIVED:
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

    def _drop_cargo(self, agv: AGV) -> None:
        """Record dropped cargo at AGV's current location and clear the load."""
        if agv.current_load and agv.current_node is not None:
            for sku, qty in agv.current_load.items():
                self._dropped_cargo.append((self.env.now, agv.current_node, sku, qty))
                for cb in self._hooks_on_cargo_dropped:
                    cb(agv, agv.current_node, sku, qty)
        self._set_load(agv, None)

    def _return_cargo_to_origin(self, order: TransferOrder, agv: AGV) -> ProcessGenerator:
        """Navigate AGV to origin and put cargo back. Falls back to drop if travel fails."""
        if not agv.current_load:
            return

        current_node = self._require_current_node(agv)
        origin_bay = order.origin.nearest_input_bay(current_node, self.graph)
        if current_node != origin_bay:
            self._transition_agv(agv, AGVState.TRAVELING_LOADED)
            outcome = yield from self._executor.travel(agv, origin_bay, loaded=True)
            if outcome is not TravelOutcome.ARRIVED:
                self._drop_cargo(agv)
                self._set_status(order, OrderStatus.FAILED, "cargo_dropped")
                return

        for sku, qty in agv.current_load.items():
            yield from order.origin.put(sku, qty)
        self._set_load(agv, None)

    def _find_nearest_charger(self, agv: AGV) -> ChargingStation | None:
        """Find the nearest charging station by graph distance."""
        if not self.charging_stations or agv.current_node is None:
            return None

        def _distance(cs: ChargingStation) -> float:
            assert agv.current_node is not None
            path = self.graph.shortest_path(agv.current_node, cs.node)
            if path is None:
                return float("inf")
            return self.graph.path_distance(path)

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
            total_dist = self.graph.path_distance(path)
            # Estimate energy cost to get there
            est_travel_time = agv.agv_type.speed_profile.travel_time(total_dist, 0.0, agv.battery.level_pct)
            est_avg_speed = total_dist / est_travel_time if est_travel_time > 0 else 0.0
            energy_needed = agv.battery.estimate_energy(total_dist, 0.0, est_avg_speed)
            if agv.battery.level >= energy_needed:
                reachable.append((total_dist, cs))

        if not reachable:
            return None
        return min(reachable, key=lambda x: x[0])[1]

    def _check_pending_queue(self) -> None:
        """Try pending orders; a saturated fleet need not rescan its compatible backlog.

        Built-in strategies only select idle AGVs. Custom strategies (including
        subclasses) keep receiving the full fleet on every retry tick.
        """
        if not self._pending_queue:
            return

        builtin = type(self._dispatch_strategy) in (NearestIdleStrategy, RoundRobinStrategy)
        idle = [agv for agv in self.fleet if agv.state == AGVState.IDLE] if builtin else []
        if builtin and not idle:
            # Impossible orders must still exhaust their retries while all AGVs
            # are busy. Compatible orders wait for availability without spending
            # the retry budget or walking the backlog on every timer tick.
            orders: list[TransferOrder] | deque[TransferOrder] = list(self._pending_unserviceable.values())
        elif not builtin or self._hooks_on_order_dispatched:
            # User dispatch hooks may submit/cancel orders while this scan runs.
            orders = list(self._pending_queue)
        else:
            # No user dispatch hooks can mutate the deque during iteration.
            # Remove dispatched/failed orders only after the scan.
            orders = self._pending_queue

        dispatched: list[TransferOrder] = []
        failed: list[TransferOrder] = []
        for order in orders:
            if builtin and not idle and order.id not in self._pending_unserviceable:
                break
            candidates = idle if builtin else self.fleet
            agv = self._dispatch_strategy.select(order, candidates, self.graph)
            if agv is not None:
                dispatched.append(order)
                self._dispatch_retries.pop(order.id, None)
                self._dispatch(order, agv)
                if builtin:
                    idle = [a for a in idle if a.state == AGVState.IDLE]
            else:
                capable_agvs = [a for a in self.fleet if a.can_carry(order.sku, order.quantity)]
                if capable_agvs:
                    self._pending_unserviceable.pop(order.id, None)
                    if not any(a.state == AGVState.IDLE for a in capable_agvs):
                        continue
                elif builtin:
                    self._pending_unserviceable[order.id] = order
                self._dispatch_retries[order.id] = self._dispatch_retries.get(order.id, 0) + 1
                if self._dispatch_retries[order.id] >= self._max_dispatch_retries:
                    failed.append(order)

        for order in dispatched:
            self._pending_remove(order)

        for order in failed:
            self._pending_remove(order)
            self._set_status(order, OrderStatus.FAILED, "retries_exhausted")
            self._retire_if_terminal(order)

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
