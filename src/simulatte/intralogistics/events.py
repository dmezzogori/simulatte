"""Events of the intralogistics subsystem (spec §6.4, §6.5): fleet, AGVs, transfer orders, traffic, warehouses,
charging stations and parking areas.

Every event is emitted after the state change it describes, and its deltas carry every change of ``agv``,
``order``, ``fleet``, ``node``, ``warehouse``, ``charging_station`` and ``parking_area`` state made since the
previous event, so replaying the deltas reproduces the live state at each event. Payload values come from data the
transition already computed.
"""

from __future__ import annotations

from simulatte._wire import FrozenMap
from simulatte.events import DomainEvent, event_type

__all__ = [
    "AgvBatteryChanged",
    "AgvLoadChanged",
    "AgvMoveEnded",
    "AgvMoveInterrupted",
    "AgvMoveStarted",
    "AgvPlaced",
    "AgvStateChanged",
    "AgvStranded",
    "ChargingEnded",
    "ChargingPoolChanged",
    "ChargingStarted",
    "FleetAgvAdded",
    "FleetPendingChanged",
    "OrderAssigned",
    "OrderStatusChanged",
    "OrderUnassigned",
    "ParkingEntered",
    "ParkingLeft",
    "TrafficReleased",
    "TrafficReserved",
    "TrafficWaitEnded",
    "TrafficWaitStarted",
    "WarehouseInventoryChanged",
    "WarehouseSlotChanged",
]


# --- fleet -------------------------------------------------------------------------------------------------


@event_type("fleet.agv_added", touches={"agv": ("fleet",)})
class FleetAgvAdded(DomainEvent):
    """A fleet coordinator took the AGV at construction; the AGV's owner field ``fleet`` becomes its id."""

    fleet: str
    agv: str


@event_type("fleet.pending_changed", touches={"fleet": ("pending",)})
class FleetPendingChanged(DomainEvent):
    """An order entered (`op` ``"added"``, ``insert`` at `index`) or left (``"removed"``, from `index`) the pending
    queue of the fleet."""

    fleet: str
    order: str
    op: str
    index: int


# --- orders ------------------------------------------------------------------------------------------------


@event_type("order.status_changed", touches={"order": ("status", "dispatched_at", "delivered_at", "agv")})
class OrderStatusChanged(DomainEvent):
    """The status of an order was assigned; `previous` may equal `status` when a site reassigns the same value.

    At dispatch this event precedes the ``order.assigned`` event that links the order and the AGV, so an observer
    of this event must take the link from the next event, not from the live order.

    The deltas set ``status`` and, for ``DISPATCHED`` and ``COMPLETED``, the matching timestamp
    (``dispatched_at``, ``delivered_at``); with reason ``load_recovery`` they also set the order's ``agv`` when the
    strategy changed it together with the status (``ReturnToOrigin`` clears it). `reason` names the transition:
    ``awaiting_activation``, ``no_idle_agv``, ``dispatched``, ``arrived_at_origin``, ``picked``,
    ``arrived_at_destination``, ``delivered``, ``interrupted`` (re-queued before pickup), ``cancelled``,
    ``load_recovery`` (set by the load-recovery strategy), ``battery_stranded``, ``travel_failed``, ``cargo_dropped``
    or ``retries_exhausted``.
    """

    order: str
    status: str
    previous: str
    reason: str


@event_type("order.assigned", touches={"order": ("agv",), "agv": ("order",)})
class OrderAssigned(DomainEvent):
    """The order was dispatched to the AGV: the order's ``agv`` and the AGV's ``order`` point to each other."""

    order: str
    agv: str


@event_type("order.unassigned", touches={"order": ("agv",), "agv": ("order",)})
class OrderUnassigned(DomainEvent):
    """One side of an order-AGV link was cleared; the deltas say which.

    The order's ``agv`` is cleared when the order goes back to the pending queue after an interruption, or when the
    load-recovery strategy clears it without changing the status; the AGV's ``order`` is cleared at mission cleanup
    (completion, failure, cancellation, interruption). A completed or failed order keeps its ``agv``.
    """

    order: str
    agv: str


# --- AGVs --------------------------------------------------------------------------------------------------


@event_type("agv.state_changed", touches={"agv": ("state",)})
class AgvStateChanged(DomainEvent):
    """``AGV.transition_to`` ran; `previous` equals `state` when a mission re-enters its state (after charging)."""

    agv: str
    state: str
    previous: str


@event_type("agv.placed", touches={"agv": ("node",), "node": ("agvs",)})
class AgvPlaced(DomainEvent):
    """The AGV was located at `node` without a movement segment.

    Emitted when an AGV is created at a node that is already bound (the node's ``agvs`` gains the AGV) and when
    ``current_node`` is assigned directly. Movement along the graph emits :class:`AgvMoveEnded` instead.
    """

    agv: str
    node: str | None
    previous: str | None


@event_type("agv.move_started", touches={"agv": ("motion",)})
class AgvMoveStarted(DomainEvent):
    """The AGV started a segment from `from_node` to `to_node` (spec §6.5), after the traffic manager let it in.

    `motion` is the speed profile's motion description; the AGV's ``motion`` state holds the active segment.
    `t_end` is ``+inf`` for a stalled segment (a non-finite travel time).
    """

    agv: str
    from_node: str
    to_node: str
    t_end: float
    motion: FrozenMap
    loaded: bool


@event_type("agv.move_ended", touches={"agv": ("node", "battery", "motion"), "node": ("agvs",)})
class AgvMoveEnded(DomainEvent):
    """The AGV reached `node` at the end of a segment; `battery` is its level after the segment's depletion."""

    agv: str
    node: str
    battery: float


@event_type("agv.move_interrupted", touches={"agv": ("motion",)})
class AgvMoveInterrupted(DomainEvent):
    """The mission was interrupted during a segment; the AGV stays at `node`, the node it left.

    `reason` is the interrupt cause when it is a string, otherwise ``"interrupted"``.
    """

    agv: str
    node: str | None
    reason: str


@event_type("agv.load_changed", touches={"agv": ("load",), "order": ("picked_at",)})
class AgvLoadChanged(DomainEvent):
    """The AGV's cargo changed: loaded at pickup (the deltas also set the order's ``picked_at``), unloaded at
    delivery, returned to the origin or dropped."""

    agv: str
    load: FrozenMap | None


@event_type("agv.battery_changed", touches={"agv": ("battery",)})
class AgvBatteryChanged(DomainEvent):
    """The battery level changed outside movement: a recharge or a battery swap at a charging station."""

    agv: str
    battery: float


@event_type("agv.stranded")
class AgvStranded(DomainEvent):
    """The AGV is stranded at `node` without enough energy for its next segment.

    `reason` is ``"no_reachable_charger"`` or ``"insufficient_after_charging"``. It changes no state: the
    preceding ``agv.state_changed`` to ``STRANDED`` carries the state change.
    """

    agv: str
    node: str
    reason: str


# --- traffic -----------------------------------------------------------------------------------------------


@event_type("traffic.reserved", touches={"node": ("reserved_by",)})
class TrafficReserved(DomainEvent):
    """The traffic manager recorded a reservation of `node` for the AGV: at its initial placement (``place_now``)
    or when ``enter_node`` resumed after the grant. The node's ``reserved_by`` gains the AGV."""

    agv: str
    node: str


@event_type("traffic.released", touches={"node": ("reserved_by",)})
class TrafficReleased(DomainEvent):
    """The AGV left `node` (``leave_node`` released a recorded reservation); the node's ``reserved_by`` loses it."""

    agv: str
    node: str


@event_type("traffic.wait_started")
class TrafficWaitStarted(DomainEvent):
    """The AGV started waiting before entering `node`, the next node it means to enter.

    `reason` is ``"node_occupied"`` (``enter_node`` found the node fully reserved), ``"path_delay"`` (the traffic
    manager's path check asked to wait until a later time) or ``"deadlock_backoff"`` (the coordinator backs off
    after a deadlock timeout found no alternative route). It changes no state.
    """

    agv: str
    node: str
    reason: str


@event_type("traffic.wait_ended")
class TrafficWaitEnded(DomainEvent):
    """A wait announced by ``traffic.wait_started`` ended; `node` is the same node.

    `reason` is the outcome: ``"granted"`` (the node was reserved; ``traffic.reserved`` follows), ``"cancelled"``
    (the traffic manager withdrew the waiting request), ``"interrupted"`` (the waiting process was interrupted) or
    ``"elapsed"`` (a delay or a backoff ran out). It changes no state.
    """

    agv: str
    node: str
    reason: str


# --- warehouses --------------------------------------------------------------------------------------------


@event_type("warehouse.inventory_changed", touches={"warehouse": ("inventory",)})
class WarehouseInventoryChanged(DomainEvent):
    """The inventory of `sku` changed by `delta` (positive for a put, negative for a get) to `level`.

    Emitted by the inventory container itself when SimPy completes the put or the get, which for a get that waited
    for stock happens when a later put is processed.
    """

    warehouse: str
    sku: str
    level: float
    delta: float


@event_type("warehouse.slot_changed", touches={"warehouse": ("slots_in_use",)})
class WarehouseSlotChanged(DomainEvent):
    """A pick or put slot was acquired or released; `in_use` is the number of slots in use afterwards."""

    warehouse: str
    in_use: int


# --- charging stations -------------------------------------------------------------------------------------


@event_type("charging.started", touches={"charging_station": ("slots_in_use",)})
class ChargingStarted(DomainEvent):
    """The AGV was granted a slot of the charging station; `mode` is ``"recharge"`` or ``"swap"``.

    The deltas set the station's ``slots_in_use``. A swap then takes a battery from the pool, which emits
    ``charging.pool_changed``.
    """

    station: str
    agv: str
    mode: str


@event_type("charging.ended", touches={"charging_station": ("slots_in_use",)})
class ChargingEnded(DomainEvent):
    """The AGV released its slot of the charging station (completed or interrupted); the deltas set
    ``slots_in_use``."""

    station: str
    agv: str
    mode: str


@event_type("charging.pool_changed", touches={"charging_station": ("swap_pool",)})
class ChargingPoolChanged(DomainEvent):
    """The swap pool of the station changed: a swap took a charged battery, or ``_replenish_pool`` returned one.

    `swap_pool` is the number of charged batteries in the pool afterwards.
    """

    station: str
    swap_pool: float


# --- parking areas -----------------------------------------------------------------------------------------


@event_type("parking.entered", touches={"parking_area": ("parked",)})
class ParkingEntered(DomainEvent):
    """The AGV took a slot of the parking area (``ParkingArea.enter``); the area's ``parked`` gains it."""

    area: str
    agv: str


@event_type("parking.left", touches={"parking_area": ("parked",)})
class ParkingLeft(DomainEvent):
    """The AGV released its parking slot (``ParkingArea.leave``); the area's ``parked`` loses it."""

    area: str
    agv: str
