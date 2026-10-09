"""Events emitted by the fleet coordinator, AGVs and transfer orders (spec §6.4, §6.5).

Every event is emitted after the state change it describes, and its deltas carry every change of ``agv``,
``order`` and ``fleet`` state made since the previous event, so replaying the deltas reproduces the live state
at each event. Payload values come from data the transition already computed.
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
    "FleetAgvAdded",
    "FleetPendingChanged",
    "OrderAssigned",
    "OrderStatusChanged",
    "OrderUnassigned",
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
