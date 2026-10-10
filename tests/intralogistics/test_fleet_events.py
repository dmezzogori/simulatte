"""Fleet, AGV and order events (spec §6.4, S6): every mutation of ``agv``, ``order`` and ``fleet`` state has an event.

Mutation sites (functions of ``fleet.py`` unless another file is named) and the event whose deltas carry each:

``agv`` state

==========  ========================================================================  ==============================
field       site                                                                      event
==========  ========================================================================  ==============================
node        ``agv.py`` ``AGV.__init__``: created at a node already bound (the node's   ``agv.placed`` (node ``agvs``)
            ``agvs`` gains the AGV; its own ``node`` is in its ``entity.created``)
node        ``agv.py`` ``current_node`` setter: direct assignment                     ``agv.placed``
node        ``_travel`` -> ``_end_segment``: ``agv._relocate(next_node)`` after the    ``agv.move_ended``
            travel timeout
state       ``agv.py`` ``AGV.transition_to``, the only writer; every                  ``agv.state_changed``
            ``_transition_agv`` of ``_dispatch``, ``_run_mission``, ``_travel``,
            ``_charge_agv`` and ``_return_cargo_to_origin`` goes through it
battery     ``_travel``: ``battery.deplete`` after the segment (now after             ``agv.move_ended``
            ``leave_node``, so position and battery change in one event)
battery     ``charging.py`` ``recharge``: ``battery.recharge``; ``swap``:              ``agv.battery_changed``
            ``battery.level = capacity``
load        ``_run_mission`` pickup: ``_set_load(agv, {sku: qty}, picked=order)``     ``agv.load_changed``
load        ``_run_mission`` delivery and the interrupt handler's resumed delivery:    ``agv.load_changed``
            ``_set_load(agv, None)``
load        ``_drop_cargo`` and ``_return_cargo_to_origin``: ``_set_load(agv, None)``  ``agv.load_changed``
order       ``_dispatch``: ``agv.order = order``                                      ``order.assigned``
order       ``_run_mission`` ``finally``: ``agv.order = None`` while it is still the   ``order.unassigned``
            mission's order
motion      ``_travel`` -> ``_start_segment``: after ``enter_node`` admitted the AGV  ``agv.move_started``
motion      ``_travel`` -> ``_end_segment``                                           ``agv.move_ended``
motion      ``_travel`` ``except simpy.Interrupt`` -> ``_interrupt_segment``          ``agv.move_interrupted``
fleet       ``FleetCoordinator.__init__``: ``agv.fleet = self``                        ``fleet.agv_added``
==========  ========================================================================  ==============================

``order`` state (``order.status_changed`` reasons in parentheses)

=============  ======================================================================  ===============================
field          site                                                                    event
=============  ======================================================================  ===============================
status         ``submit`` before activation: ``PENDING_ACTIVATION``                    status (awaiting_activation)
status         ``_submit``, no idle AGV: ``PENDING``                                   status (no_idle_agv)
status         ``cancel``, pending order: ``CANCELLED``                                status (cancelled)
status         ``cancel``, active order or no mission: ``CANCELLED``                   status (cancelled)
status         ``_dispatch``: ``DISPATCHED`` with ``dispatched_at``                    status (dispatched)
status         ``_run_mission`` empty travel stranded / failed: ``FAILED``             status (battery_stranded,
                                                                                       travel_failed)
status         ``_run_mission``: ``PICKING``                                           status (arrived_at_origin)
status         ``_run_mission``: ``IN_TRANSIT``                                        status (picked)
status         ``_run_mission`` loaded travel stranded / failed: ``FAILED``            status (battery_stranded,
                                                                                       travel_failed)
status         ``_run_mission``: ``DELIVERING``                                        status (arrived_at_destination)
status         ``_run_mission``: ``COMPLETED`` with ``delivered_at``                   status (delivered)
status, agv    interrupt handler, ``load_recovery_strategy.recover`` (``policies.py``  status (load_recovery), with
               ``ReturnToOrigin``: ``PENDING``, ``assigned_agv = None``;               the ``agv`` delta; an ``agv``
               ``ResumeDelivery``: ``IN_TRANSIT``); changes seen after it returns       change alone: order.unassigned
status         interrupt handler, resumed travel stranded / failed: ``FAILED``         status (battery_stranded,
                                                                                       travel_failed)
status         interrupt handler, resumed delivery: ``DELIVERING``, ``COMPLETED``      status (arrived_at_destination,
               with ``delivered_at``                                                   delivered)
status         interrupt handler, before pickup: ``PENDING``                           status (interrupted)
agv            interrupt handler, before pickup: ``assigned_agv = None``               order.unassigned
status         interrupt handler, cancellation: ``CANCELLED`` reasserted               status (cancelled)
status         ``_return_cargo_to_origin``, travel failed: ``FAILED``                  status (cargo_dropped)
status         ``_check_pending_queue``, retries exhausted: ``FAILED``                 status (retries_exhausted)
agv            ``_dispatch``: ``assigned_agv = agv``                                   order.assigned
dispatched_at  ``_dispatch``                                                           status (dispatched)
picked_at      ``_run_mission`` pickup (``_set_load(..., picked=order)``)              agv.load_changed
delivered_at   ``_run_mission`` and the resumed delivery, before ``COMPLETED``         status (delivered)
others         ``sku``, ``quantity``, ``origin``, ``destination``, ``created_at``,     entity.created
               ``fleet``: set before attachment
=============  ======================================================================  ===============================

``fleet`` state: ``pending`` changes in ``_pending_add`` (``_submit``, the re-queue of the interrupt handler) and
``_pending_remove`` (``cancel``, ``_check_pending_queue`` for dispatched and failed orders): ``fleet.pending_changed``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from simulatte.entities import EntityRetired
from simulatte.environment import Environment
from simulatte.events import DomainEvent, apply_deltas
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.events import (
    AgvBatteryChanged,
    AgvPlaced,
    AgvStateChanged,
    FleetAgvAdded,
    FleetPendingChanged,
    OrderAssigned,
    OrderStatusChanged,
    OrderUnassigned,
)
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.policies import ReorderPointPolicy, ResumeDelivery, RoundRobinStrategy
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.warehouse import Warehouse

from tests.intralogistics.test_entities import SKU_A, _agv_type, _line_graph, _system

FLEET_KINDS = frozenset({"agv", "order", "fleet"})


class FleetReplay:
    """Applies the deltas of every domain event and compares the replayed state of `kinds` with the live registry.

    By default only ``agv``, ``order`` and ``fleet`` entities are compared; ``test_resource_events.py`` compares
    every kind.
    """

    def __init__(self, env: Environment, kinds: frozenset[str] = FLEET_KINDS) -> None:
        self.env = env
        self.kinds = kinds
        self.state: dict[str, dict[str, Any]] = {}
        self.events = 0
        self.types: list[str] = []
        env.bus.subscribe(self, "*")

    def view(self, state: dict[str, Any]) -> dict[str, Any]:
        return {entity: dict(fields) for entity, fields in state.items() if fields["$kind"] in self.kinds}

    def __call__(self, event: DomainEvent) -> None:
        apply_deltas(self.state, event.deltas)
        assert self.view(self.state) == self.view(self.env.entities.snapshot()), event
        self.events += 1
        self.types.append(event.type_name)


# --- scenarios: one per status assignment site -------------------------------------------------------------

ISLAND = Node(id="ISLAND", x=100.0, y=100.0)


def _fleet(
    env: Environment,
    *,
    battery: float = 1000.0,
    origin_bay: str = "OUT",
    origin_input: str | None = None,
    destination_bay: str = "IN",
    **options: Any,
) -> tuple[FleetCoordinator, AGV, Warehouse, Warehouse]:
    """The line OUT - C1 - C2 - IN plus an unreachable ISLAND; one AGV at C1; WH-A and WH-B on the named nodes."""
    nodes, _ = _line_graph()
    by_id = {node.id: node for node in [*nodes, ISLAND]}
    graph = LayoutGraph([*nodes, ISLAND], [Arc(source=a, target=b) for a, b in zip(nodes, nodes[1:], strict=False)])

    def warehouse(name: str, out_bay: str, in_bay: str, level: int) -> Warehouse:
        return Warehouse(
            env=env,
            name=name,
            input_bays=[by_id[in_bay]],
            output_bays=[by_id[out_bay]],
            n_slots=2,
            products=[SKU_A],
            initial_inventory={SKU_A: level},
            pick_time=1.0,
            put_time=1.0,
        )

    wh_a = warehouse("WH-A", origin_bay, origin_input or origin_bay, 100)
    wh_b = warehouse("WH-B", destination_bay, destination_bay, 0)
    agv_type = AGVType(
        name="t",
        speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
        battery_capacity=battery,
        weight_capacity=100.0,
        volume_capacity=10.0,
        load_time=1.0,
        unload_time=1.0,
    )
    agv = AGV(env=env, agv_type=agv_type, initial_node=by_id["C1"])
    coordinator = FleetCoordinator(
        env=env, graph=graph, fleet=[agv], warehouses=[wh_a, wh_b], charging_stations=[], **options
    )
    return coordinator, agv, wh_a, wh_b


def _order(coordinator: FleetCoordinator, wh_a: Warehouse, wh_b: Warehouse, sku: SKU = SKU_A) -> TransferOrder:
    return coordinator.create_order(sku=sku, quantity=1, origin=wh_a, destination=wh_b)


def _interrupt_at(env: Environment, coordinator: FleetCoordinator, order: TransferOrder, t: float, cause: str) -> None:
    env.run(until=t)
    coordinator._active_missions[order.id].interrupt(cause)


# Timeline of the default scenario: C1 -> OUT ends at 4.5, pick until 5.5, load until 6.5 (loaded, PICKING).


def _awaiting_activation(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env)
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    env.run()
    return order


def _no_idle_agv(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env)
    env.activate()
    coordinator.submit(_order(coordinator, wh_a, wh_b))
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    env.run()
    return order


def _delivered(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env)
    env.activate()
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    env.run()
    return order


def _cancel_pending(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env)
    env.activate()
    coordinator.submit(_order(coordinator, wh_a, wh_b))
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    coordinator.cancel(order)
    env.run()
    return order


def _cancel_active(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env)
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    env.run(until=2.0)
    coordinator.cancel(order)
    env.run()
    return order


def _cancel_return_fails(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env, origin_input="ISLAND")  # the cargo cannot go back
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    env.run(until=8.0)  # loaded, OUT -> C1
    coordinator.cancel(order)
    env.run()
    return order


def _requeued(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env)
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    _interrupt_at(env, coordinator, order, 2.0, "breakdown")
    env.run()
    return order


def _return_to_origin(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env)
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    _interrupt_at(env, coordinator, order, 6.0, "breakdown")
    env.run()
    return order  # left PENDING and never re-queued (pre-existing, ruling R20)


def _resume_delivery(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env, load_recovery_strategy=ResumeDelivery())
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    _interrupt_at(env, coordinator, order, 6.0, "breakdown")
    env.run()
    return order


def _resume_stranded(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env, battery=12.0, load_recovery_strategy=ResumeDelivery())
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    _interrupt_at(env, coordinator, order, 6.0, "breakdown")
    env.run()
    return order


def _resume_travel_failed(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env, destination_bay="ISLAND", load_recovery_strategy=ResumeDelivery())
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    _interrupt_at(env, coordinator, order, 6.0, "breakdown")
    env.run()
    return order


def _stranded_empty(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env, battery=3.0)  # C1 -> OUT needs 5
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    env.run()
    return order


def _stranded_loaded(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env, battery=12.0)  # strands at C1 on the way back, cargo dropped
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    env.run()
    return order


def _travel_failed_empty(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env, origin_bay="ISLAND", dispatch_strategy=RoundRobinStrategy())
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    env.run()
    return order


def _travel_failed_loaded(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env, destination_bay="ISLAND")
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    env.run()
    return order


def _retries_exhausted(env: Environment) -> TransferOrder:
    coordinator, _, wh_a, wh_b = _fleet(env, max_dispatch_retries=2, pending_retry_delay=0.5)
    heavy = SKU(id="HEAVY", weight=1000.0, volume=0.1)
    wh_a.inventory[heavy] = type(wh_a.inventory[SKU_A])(env, init=5)
    order = _order(coordinator, wh_a, wh_b, heavy)
    coordinator.submit(order)
    env.run()
    return order


PA, P, D, PI, T, DL, C, F, X = (
    "PENDING_ACTIVATION",
    "PENDING",
    "DISPATCHED",
    "PICKING",
    "IN_TRANSIT",
    "DELIVERING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
DELIVERY = [(D, PA, "dispatched"), (PI, D, "arrived_at_origin"), (T, PI, "picked")]  # submitted before activation

STATUS_SITES: list[Any] = [
    pytest.param(
        _awaiting_activation, [(PA, P, "awaiting_activation"), (D, PA, "dispatched")], id="awaiting_activation"
    ),
    pytest.param(_no_idle_agv, [(P, P, "no_idle_agv"), (D, P, "dispatched")], id="no_idle_agv"),
    pytest.param(
        _delivered,
        [(D, P, "dispatched"), *DELIVERY[1:], (DL, T, "arrived_at_destination"), (C, DL, "delivered")],
        id="dispatch_to_delivery",
    ),
    pytest.param(_cancel_pending, [(P, P, "no_idle_agv"), (X, P, "cancelled")], id="cancel_pending"),
    pytest.param(_cancel_active, [(X, D, "cancelled"), (X, X, "cancelled")], id="cancel_active"),
    pytest.param(
        _cancel_return_fails,
        [(X, T, "cancelled"), (F, X, "cargo_dropped"), (X, F, "cancelled")],
        id="cancel_reasserted_after_return_failed",
    ),
    pytest.param(_requeued, [(P, D, "interrupted"), (D, P, "dispatched"), (C, DL, "delivered")], id="interrupted"),
    pytest.param(_return_to_origin, [(PI, D, "arrived_at_origin"), (P, PI, "load_recovery")], id="return_to_origin"),
    pytest.param(
        _resume_delivery,
        [(T, PI, "load_recovery"), (DL, T, "arrived_at_destination"), (C, DL, "delivered")],
        id="resume_delivery",
    ),
    pytest.param(
        _resume_stranded,
        [(T, PI, "load_recovery"), (F, T, "cargo_dropped"), (F, F, "battery_stranded")],
        id="resume_stranded",
    ),
    pytest.param(_resume_travel_failed, [(T, PI, "load_recovery"), (F, T, "travel_failed")], id="resume_failed"),
    pytest.param(_stranded_empty, [(D, PA, "dispatched"), (F, D, "battery_stranded")], id="stranded_empty"),
    pytest.param(
        _stranded_loaded, [*DELIVERY, (F, T, "cargo_dropped"), (F, F, "battery_stranded")], id="stranded_loaded"
    ),
    pytest.param(_travel_failed_empty, [(D, PA, "dispatched"), (F, D, "travel_failed")], id="travel_failed_empty"),
    pytest.param(_travel_failed_loaded, [*DELIVERY, (F, T, "travel_failed")], id="travel_failed_loaded"),
    pytest.param(_retries_exhausted, [(P, PA, "no_idle_agv"), (F, P, "retries_exhausted")], id="retries_exhausted"),
]


@pytest.mark.parametrize(("scenario", "expected"), STATUS_SITES)
def test_every_order_status_assignment_emits(
    scenario: Callable[[Environment], TransferOrder], expected: list[tuple[str, str, str]]
) -> None:
    env = Environment(debug=True)
    replay = FleetReplay(env)  # the live state equals the replay at every event
    changes: list[OrderStatusChanged] = []
    env.bus.subscribe(changes.append, (OrderStatusChanged,))
    order = scenario(env)

    seen = [(e.status, e.previous, e.reason) for e in changes if e.order == order.id]
    remaining = iter(seen)
    assert all(triple in remaining for triple in expected), seen  # in this order
    assert seen[-1][0] == order.status.name  # the last event states the final status
    assert replay.events > 0
    for event in changes:
        stamp = {"DISPATCHED": "dispatched_at", "COMPLETED": "delivered_at"}.get(event.status)
        fields = [op[2] for op in event.deltas.ops]
        assert fields[0] == "status" and (stamp is None or stamp in fields)


def test_status_sites_cover_every_reason() -> None:
    reasons = {triple[2] for param in STATUS_SITES for triple in param.values[1]}
    assert reasons == {
        "awaiting_activation",
        "no_idle_agv",
        "dispatched",
        "arrived_at_origin",
        "picked",
        "arrived_at_destination",
        "delivered",
        "interrupted",
        "cancelled",
        "load_recovery",
        "battery_stranded",
        "travel_failed",
        "cargo_dropped",
        "retries_exhausted",
    }
    assert all(f"``{reason}``" in (OrderStatusChanged.__doc__ or "") for reason in reasons)


def test_return_to_origin_status_event_carries_the_cleared_agv() -> None:
    env = Environment(debug=True)
    FleetReplay(env)
    changes: list[DomainEvent] = []
    env.bus.subscribe(changes.append, (OrderStatusChanged, OrderUnassigned))
    order = _return_to_origin(env)
    recovery = next(e for e in changes if isinstance(e, OrderStatusChanged) and e.reason == "load_recovery")
    assert recovery.deltas.ops == (("set", order.id, "status", "PENDING"), ("set", order.id, "agv", None))
    assert order.assigned_agv is None and order.status is OrderStatus.PENDING


def _recovery_changing_only_the_agv(env: Environment, reassign: bool) -> tuple[TransferOrder, AGV, AGV]:
    spare = AGV(env=env, agv_type=_agv_type())

    class Strategy:
        def recover(self, order: TransferOrder, agv: AGV, coordinator: FleetCoordinator) -> Any:
            order.assigned_agv = spare if reassign else None
            yield from ()

    coordinator, agv, wh_a, wh_b = _fleet(env, load_recovery_strategy=Strategy())
    order = _order(coordinator, wh_a, wh_b)
    coordinator.submit(order)
    _interrupt_at(env, coordinator, order, 6.0, "breakdown")
    env.run()
    return order, agv, spare


@pytest.mark.parametrize("reassign", [False, True])
def test_load_recovery_changing_only_the_agv(reassign: bool) -> None:
    """A strategy that changes the order's AGV without changing its status emits the link event alone."""
    env = Environment(debug=True)
    FleetReplay(env)
    links: list[DomainEvent] = []
    env.bus.subscribe(links.append, (OrderAssigned, OrderUnassigned))
    order, agv, spare = _recovery_changing_only_the_agv(env, reassign)

    recovery = links[1]
    if reassign:
        assert isinstance(recovery, OrderAssigned) and recovery.agv == spare.id
        assert recovery.deltas.ops == (("set", order.id, "agv", spare.id),)
    else:
        assert isinstance(recovery, OrderUnassigned) and recovery.agv == agv.id
        assert recovery.deltas.ops == (("set", order.id, "agv", None),)
    assert order.status is OrderStatus.PICKING  # unchanged by the strategy; the cargo went back to the origin

    quiet, _, quiet_spare = _recovery_changing_only_the_agv(Environment(), reassign)  # nobody listening
    assert quiet.status is OrderStatus.PICKING
    assert quiet.assigned_agv is (quiet_spare if reassign else None)


# --- order-AGV link ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", [_delivered, _cancel_active, _stranded_empty, _travel_failed_loaded])
def test_agv_unassigned_on_cleanup(scenario: Callable[[Environment], TransferOrder]) -> None:
    env = Environment(debug=True)
    FleetReplay(env)
    links: list[DomainEvent] = []
    env.bus.subscribe(links.append, (OrderAssigned, OrderUnassigned, EntityRetired))
    order = scenario(env)
    agv = order.assigned_agv
    assert agv is not None  # a finished order keeps its AGV

    ours = [e for e in links if getattr(e, "order", getattr(e, "entity", None)) == order.id]
    assert [e.type_name for e in ours] == ["order.assigned", "order.unassigned", "entity.retired"]
    assigned, unassigned = ours[0], ours[1]
    assert assigned.deltas.ops == (("set", order.id, "agv", agv.id), ("set", agv.id, "order", order.id))
    assert unassigned.deltas.ops == (("set", agv.id, "order", None),)
    assert agv.order is None


def test_requeue_unassigns_the_order_then_the_agv() -> None:
    env = Environment(debug=True)
    FleetReplay(env)
    links: list[DomainEvent] = []
    env.bus.subscribe(links.append, (OrderAssigned, OrderUnassigned))
    order = _requeued(env)
    agv = order.assigned_agv
    assert agv is not None
    assert [e.deltas.ops for e in links] == [
        (("set", order.id, "agv", agv.id), ("set", agv.id, "order", order.id)),
        (("set", order.id, "agv", None),),  # back to the queue
        (("set", agv.id, "order", None),),  # mission cleanup
        (("set", order.id, "agv", agv.id), ("set", agv.id, "order", order.id)),  # dispatched again
        (("set", agv.id, "order", None),),
    ]


# --- pending queue (T4) ------------------------------------------------------------------------------------


def test_pending_redispatch_replay_at_intermediate_cursors() -> None:
    env = Environment(debug=True)
    coordinator, (agv,), wh_a, wh_b, _ = _system(env)
    orders = [_order(coordinator, wh_a, wh_b) for _ in range(3)]
    for order in orders:
        coordinator.submit(order)

    def view(state: Any) -> dict[str, Any]:
        return {entity: dict(fields) for entity, fields in state.items() if fields["$kind"] in FLEET_KINDS}

    log: list[tuple[DomainEvent, dict[str, Any]]] = []
    env.bus.subscribe(lambda event: log.append((event, view(env.entities.snapshot()))), "*")
    env.activate()
    initial = {entity: dict(fields) for entity, fields in env.initial_state.items()}
    env.run()
    assert all(order.status is OrderStatus.COMPLETED for order in orders)

    for cursor in range(len(log)):  # seek: replay from the initial state up to each cursor
        state = {entity: dict(fields) for entity, fields in initial.items()}
        for event, _ in log[: cursor + 1]:
            apply_deltas(state, event.deltas)
        assert view(state) == log[cursor][1], (cursor, log[cursor][0])

    pending = [e for e, _ in log if isinstance(e, FleetPendingChanged)]
    assert [(e.order, e.op, e.index) for e in pending] == [
        (orders[1].id, "added", 0),
        (orders[2].id, "added", 1),
        (orders[1].id, "removed", 0),
        (orders[2].id, "removed", 0),
    ]
    # A re-dispatched order is DISPATCHED while still pending; its removal follows the dispatch events.
    redispatch = next(
        i for i, (e, _) in enumerate(log) if isinstance(e, OrderStatusChanged) and e.order == orders[1].id and e.t > 0
    )
    snapshot = log[redispatch][1]
    assert snapshot[orders[1].id]["status"] == "DISPATCHED" and orders[1].id in snapshot[coordinator.id]["pending"]


# --- owners, states, placement -----------------------------------------------------------------------------


@pytest.mark.parametrize("after_activation", [False, True])
def test_agv_fleet_owner_set_on_coordinator_construction(after_activation: bool) -> None:
    env = Environment(debug=True)
    replay = FleetReplay(env)
    if after_activation:
        env.activate()
    nodes, graph = _line_graph()
    agvs = [AGV(env=env, agv_type=_agv_type(), initial_node=nodes[i]) for i in (1, 2)]
    assert [replay.state[agv.id]["fleet"] for agv in agvs] == [None, None]
    added: list[FleetAgvAdded] = []
    env.bus.subscribe(added.append, (FleetAgvAdded,))
    coordinator = FleetCoordinator(env=env, graph=graph, fleet=agvs, warehouses=[], charging_stations=[])

    assert [(e.fleet, e.agv) for e in added] == [(coordinator.id, agv.id) for agv in agvs]
    assert [e.deltas.ops for e in added] == [(("set", agv.id, "fleet", coordinator.id),) for agv in agvs]
    assert [replay.state[agv.id]["fleet"] for agv in agvs] == [coordinator.id, coordinator.id]
    assert all(agv.fleet is coordinator for agv in agvs)
    assert all(e.ordinal is None for e in added)


def test_direct_transition_to_emits() -> None:
    env = Environment(debug=True)
    FleetReplay(env)
    agv = AGV(env=env, agv_type=_agv_type())  # no fleet
    changes: list[AgvStateChanged] = []
    env.bus.subscribe(changes.append, (AgvStateChanged,))
    agv.transition_to(AGVState.CHARGING)
    env.run(until=3)
    agv.transition_to(AGVState.CHARGING)  # re-entering the same state is a transition too
    agv.transition_to(AGVState.IDLE)

    assert [(e.t, e.agv, e.state, e.previous) for e in changes] == [
        (0, agv.id, "CHARGING", "IDLE"),
        (3, agv.id, "CHARGING", "CHARGING"),
        (3, agv.id, "IDLE", "CHARGING"),
    ]
    assert changes[0].deltas.ops == (("set", agv.id, "state", "CHARGING"),)


@pytest.mark.parametrize("after_activation", [False, True])
def test_agv_placed_at_bound_node_and_on_direct_assignment(after_activation: bool) -> None:
    """Node ``agvs`` replay matches when an AGV is created at a bound node or moved by assignment (carried T14)."""
    env = Environment(debug=True)
    replay = FleetReplay(env, FLEET_KINDS | {"node"})  # free traffic: no reservations to replay
    if after_activation:
        env.activate()
    nodes, graph = _line_graph()
    first = AGV(env=env, agv_type=_agv_type(), initial_node=nodes[0])  # before the binding: in its create delta
    FleetCoordinator(env=env, graph=graph, fleet=[first], warehouses=[], charging_stations=[])
    placed: list[AgvPlaced] = []
    env.bus.subscribe(placed.append, (AgvPlaced,))

    second = AGV(env=env, agv_type=_agv_type(), initial_node=nodes[0])  # node already bound
    unbound = AGV(env=env, agv_type=_agv_type(), initial_node=Node(id="LOOSE", x=0.0, y=0.0))
    second.current_node = nodes[3]
    second.current_node = nodes[3]  # unchanged: no event
    second.current_node = None
    unbound.current_node = nodes[1]

    assert [(e.agv, e.node, e.previous) for e in placed] == [
        (second.id, "OUT", None),
        (second.id, "IN", "OUT"),
        (second.id, None, "IN"),
        (unbound.id, "C1", "LOOSE"),
    ]
    assert placed[0].deltas.ops == (("set", second.id, "node", "OUT"), ("insert", "OUT", "agvs", 1, second.id))
    assert placed[1].deltas.ops == (
        ("set", second.id, "node", "IN"),
        ("remove", "OUT", "agvs", second.id),
        ("insert", "IN", "agvs", 0, second.id),
    )
    assert replay.state["OUT"]["agvs"] == (first.id,) and replay.state["C1"]["agvs"] == (unbound.id,)


# --- replay over a busy run --------------------------------------------------------------------------------


def test_replay_equals_live_at_every_event_fleet() -> None:
    env = Environment(seed=5, debug=True)
    replay = FleetReplay(env)  # subscribed before any entity exists: the prelude is replayed too
    coordinator, agvs, wh_a, wh_b, _ = build_simple_system(env, n_agvs=2, agv_battery_capacity=100.0)
    skus = list(wh_a.inventory)
    coordinator.add_replenishment_policy(ReorderPointPolicy({skus[0]: 1}, {skus[0]: 2}), wh_b, check_interval=40.0)
    early = [coordinator.create_order(sku=sku, quantity=1, origin=wh_a, destination=wh_b) for sku in skus]
    for order in early:
        coordinator.submit(order)
    doomed = coordinator.create_order(sku=skus[0], quantity=1, origin=wh_a, destination=wh_b)
    coordinator.submit(doomed)
    coordinator.cancel(doomed)

    env.run(until=3.0)
    coordinator._active_missions[early[0].id].interrupt("breakdown")  # re-queued before pickup
    for i in range(8):
        env.run(until=10.0 + 15.0 * i)
        order = coordinator.create_order(sku=skus[i % len(skus)], quantity=1, origin=wh_a, destination=wh_b)
        coordinator.submit(order)
        if i == 3:
            coordinator.cancel(order)
    env.run(until=400)

    types = set(replay.types)
    assert {
        "fleet.agv_added",
        "fleet.pending_changed",
        "order.status_changed",
        "order.assigned",
        "order.unassigned",
        "agv.state_changed",
        "agv.move_started",
        "agv.move_ended",
        "agv.move_interrupted",
        "agv.load_changed",
        "agv.battery_changed",
    } <= types
    assert replay.view(replay.state) == replay.view(env.entities.snapshot())
    charged = [e for e in replay.types if e == AgvBatteryChanged.type_name]
    assert charged and replay.events > 300
