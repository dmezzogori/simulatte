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
    "AgvRouted",
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
    "TrafficDeadlock",
    "TrafficReleased",
    "TrafficReserved",
    "TrafficWaitEnded",
    "TrafficWaitStarted",
    "TrafficWaitStuck",
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
    ``arrived_at_destination``, ``delivered``, ``interrupted`` (re-queued before pickup), ``traffic_requeued``
    (re-queued before pickup because the AGV gave up its travel in traffic, spec §8.2), ``cancelled``,
    ``load_recovery`` (set by the load-recovery strategy), ``battery_stranded``, ``travel_failed`` (the router found
    no route), ``cargo_dropped`` or ``retries_exhausted``.
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


@event_type("agv.state_changed", touches={"agv": ("state", "route")})
class AgvStateChanged(DomainEvent):
    """``AGV.transition_to`` ran; `previous` equals `state` when a mission re-enters its state (after charging).

    Its deltas also clear the AGV's ``route`` when a travel ended without an event of its own to clear it: a retry
    from the current position, a stranding, a give-up, the turn to a charger, a route of one node (studio map spec
    §6.2).
    """

    agv: str
    state: str
    previous: str


@event_type("agv.placed", touches={"agv": ("node", "heading"), "node": ("agvs",)})
class AgvPlaced(DomainEvent):
    """The AGV was located at `node` without a movement segment.

    Emitted when an AGV is created at a node that is already bound (the node's ``agvs`` gains the AGV) and when
    ``current_node`` is assigned directly, which makes the AGV's ``heading`` unknown (None). Movement along the graph
    emits :class:`AgvMoveEnded` instead.
    """

    agv: str
    node: str | None
    previous: str | None


@event_type("agv.routed", touches={"agv": ("route",)})
class AgvRouted(DomainEvent):
    """The AGV took the route `nodes` (node ids) from `origin` to `destination`, of router cost `cost` (spec §11.2).

    `reason` is ``"travel"`` for a new travel, or for the route back to the goal after a step aside,
    ``"blocked"`` or ``"deadlock"`` for a reroute the traffic controller staged, ``"step_aside"`` for a step aside,
    and ``"make_way"`` or ``"clear_node"`` for the coordinator's moves of those purposes. Its deltas set the AGV's
    ``route`` to ``{nodes, destination, reason}`` (studio map spec §6.2).
    """

    agv: str
    origin: str
    destination: str
    nodes: tuple[str, ...]
    cost: float
    reason: str


@event_type("agv.move_started", touches={"agv": ("motion",)})
class AgvMoveStarted(DomainEvent):
    """The AGV started a segment from `from_node` to `to_node` (spec §6.5), holding the claim that covers it.

    `motion` is the speed profile's motion description; the AGV's ``motion`` state holds the active segment.
    `t_end` is ``+inf`` for a stalled segment (a non-finite travel time).
    """

    agv: str
    from_node: str
    to_node: str
    t_end: float
    motion: FrozenMap
    loaded: bool


@event_type("agv.move_ended", touches={"agv": ("node", "battery", "motion", "heading", "route"), "node": ("agvs",)})
class AgvMoveEnded(DomainEvent):
    """The AGV reached `node` at the end of a segment; `battery` is its level after the segment's depletion and the
    idle power of its duration.

    Its deltas also set the AGV's ``heading`` to the direction of the segment, and clear its ``route`` when `node` is
    the route's last node. An AGV that passes `node` at speed starts its next segment at the same instant.

    A stalled segment (``t_end`` infinite) that is interrupted ends with this event at its departure node: `node` is the
    node the AGV never left and the deltas only clear the motion. A consumer that counts arrivals must check that the
    node changed.
    """

    agv: str
    node: str
    battery: float


@event_type("agv.move_interrupted", touches={"agv": ("route",)})
class AgvMoveInterrupted(DomainEvent):
    """The travel was interrupted and the AGV came to rest at `node` (spec §8.3).

    An interrupt received while the AGV waits for a claim takes effect at once, at the node where it waits; one
    received while it moves takes effect where it comes to rest, after ``agv.move_ended``: the end node of the
    segment for a rest-to-rest profile, the node it held last when the interrupt arrived for a run, and the departure
    node for a stalled segment, which is never left. `reason` is the
    interrupt cause when it is a string, otherwise ``"interrupted"``. It ends the travel: its deltas clear the AGV's
    ``route`` (studio map spec §6.2); ``agv.move_ended`` already cleared the motion.
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
    """The battery level changed outside movement: a recharge or a battery swap at a charging station, or idle power
    accounted at an event of the AGV outside a move (inside a move it is part of ``agv.move_ended``)."""

    agv: str
    battery: float


@event_type("agv.stranded")
class AgvStranded(DomainEvent):
    """The AGV is stranded at `node`, the node it stands on, without enough energy for its next segment.

    `reason` is ``"no_reachable_charger"`` (no charger is reachable from `node`), ``"insufficient_after_charging"`` (it
    diverted to a charger and charged, and still cannot cover the segment it turned at) or ``"charger_not_reached"``
    (it diverted to a charger and stopped before reaching it, its level critical on the way or without a route). It
    changes no state: the preceding ``agv.state_changed`` to ``STRANDED`` carries the state change.
    """

    agv: str
    node: str
    reason: str


# --- traffic -----------------------------------------------------------------------------------------------


_HOLDINGS = {"node": ("reserved_by",), "segment": ("forward", "backward"), "zone": ("reserved_by", "orientation")}


@event_type("traffic.reserved", touches=_HOLDINGS)
class TrafficReserved(DomainEvent):
    """The AGV took hold of `resources` (entity ids, in claim order): one event per grant or placement (spec §11.2).

    The deltas insert the AGV into each node's and zone's ``reserved_by`` and into each segment's ``forward`` or
    ``backward``, and set a corridor's ``orientation`` when it changes. A node without a binding is left out. A
    corridor claimed again with the orientation it already has is listed in `resources` with no delta, so an event
    can carry empty deltas.
    """

    agv: str
    resources: tuple[str, ...]


@event_type("traffic.released", touches=_HOLDINGS)
class TrafficReleased(DomainEvent):
    """The AGV let go of `resources` (entity ids): one event per release call that frees something (spec §11.2).

    The deltas remove the AGV from the lists :class:`TrafficReserved` inserted it into, and set a corridor's
    ``orientation`` back to null when its last oriented holder leaves.
    """

    agv: str
    resources: tuple[str, ...]


@event_type("traffic.wait_started", touches={"agv": ("wait",)})
class TrafficWaitStarted(DomainEvent):
    """The AGV stopped to wait before entering `node`, the next node it means to enter (spec §11.2).

    `resources` are the ids of the resources the waiting claim still needs, `blockers` the ids of the AGVs that hold
    them (then of those whose earlier claims gate it) and `reason` is ``"occupied"``. Its deltas set the AGV's ``wait``
    to ``{since, node, resources, blockers}``, `since` the event's time (studio map spec §6.2). That is what
    ``ReservationController`` does; a custom controller that emits this event must carry the same delta (and set
    ``wait`` to null with its ``traffic.wait_ended``), and change the AGV's attribute with them, whether or not the
    events are wanted (``AGV._open_wait`` and ``AGV._close_wait``), so that a replay of the deltas equals the snapshot.
    """

    agv: str
    node: str
    resources: tuple[str, ...]
    blockers: tuple[str, ...]
    reason: str


@event_type("traffic.wait_ended", touches={"agv": ("wait",)})
class TrafficWaitEnded(DomainEvent):
    """A wait announced by ``traffic.wait_started`` ended; `node` is the same node.

    `reason` is the outcome: ``"granted"`` (``traffic.reserved`` follows), ``"rerouted"``, ``"stepped_aside"`` or
    ``"gave_up"`` (the controller staged that reaction) or ``"interrupted"`` (the request was withdrawn, for example
    because the waiting travel was interrupted). Its deltas set the AGV's ``wait`` to null.
    """

    agv: str
    node: str
    reason: str


@event_type("traffic.wait_stuck")
class TrafficWaitStuck(DomainEvent):
    """The AGV's wait is stuck (spec §7.9): every blocker is idle, stranded or stalled, or waits in a wait that is
    stuck by the same rule.

    `blockers` maps each blocker's id to its disposition name (``IDLE``, ``STRANDED``, ``WAITING``), or ``STALLED``
    for an AGV whose motion is stalled. It is emitted at a review that keeps the wait waiting, when a blocker comes to
    rest or stalls and does not move (``idle``), for the waits behind a wait just reported, and for a wait with no
    review scheduled when the controller is entered for something other than a grant or a release (a wait opens, a
    pending claim is withdrawn, a review ends, an AGV comes to rest or stalls), unless a review already scheduled will
    reach it. Emitted once per wait; it is a report, not a deadlock, and changes no state.
    """

    agv: str
    blockers: FrozenMap


@event_type("traffic.deadlock")
class TrafficDeadlock(DomainEvent):
    """The waiting AGVs `agvs` (sorted ids) are deadlocked (spec §7.8).

    `victim` is the AGV whose reaction was staged and `action` that reaction (``"reroute"``, ``"step_aside"`` or
    ``"give_up"``); when no attempt succeeded `victim` is null and `action` is ``"unresolved"``, emitted once while
    the same set stands. It changes no state.
    """

    agvs: tuple[str, ...]
    victim: str | None
    action: str


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
