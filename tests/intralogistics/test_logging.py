from __future__ import annotations

from typing import Any

from simulatte.environment import Environment
from simulatte.events import DomainEvent
from simulatte.intralogistics.agv import AGV
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.events import (
    AgvMoveStarted,
    AgvStateChanged,
    OrderStatusChanged,
    WarehouseInventoryChanged,
    WarehouseSlotChanged,
)
from simulatte.intralogistics.graph import Node


class _Unreachable:
    """Repositions every AGV to a node outside the graph, so the fleet logs an error and a warning."""

    def reposition(self, agv: AGV, context: Any) -> Node:
        return Node(id="NOWHERE", x=99.0, y=99.0)


def _run_one_order(env: Environment, *, unreachable_parking: bool = False) -> None:
    coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env, n_agvs=1)
    if unreachable_parking:
        coordinator._repositioning_policy = _Unreachable()
    sku = list(wh_a.inventory.keys())[0]
    order = coordinator.create_order(
        sku=sku,
        quantity=1,
        origin=wh_a,
        destination=wh_b,
    )
    coordinator.submit(order)
    env.run()


def test_intralogistics_transitions_are_events() -> None:
    """Fleet, AGV and warehouse transitions are domain events, not debug logs (spec §7.2): even at DEBUG, a run
    without errors records no intralogistics log message."""
    env = Environment(log_level="DEBUG", log_history_size=5000)
    seen: list[DomainEvent] = []
    env.bus.subscribe(
        seen.append,
        (OrderStatusChanged, AgvStateChanged, AgvMoveStarted, WarehouseInventoryChanged, WarehouseSlotChanged),
    )
    _run_one_order(env)

    assert {event.type_name for event in seen} == {
        "order.status_changed",
        "agv.state_changed",
        "agv.move_started",
        "warehouse.inventory_changed",
        "warehouse.slot_changed",
    }
    assert [e.status for e in seen if isinstance(e, OrderStatusChanged)][-1] == "COMPLETED"
    for component in ("FleetCoordinator", "AGV", "Warehouse", "TrafficManager", "ChargingStation", "ParkingArea"):
        assert env.log_history.query(component=component) == [], component


def test_disable_component_suppresses_its_logs() -> None:
    """``env.log_history.disable_component("FleetCoordinator")`` suppresses the fleet's errors and warnings, which
    are recorded otherwise."""
    muted = Environment(log_history_size=5000)
    muted.log_history.disable_component("FleetCoordinator")
    _run_one_order(muted, unreachable_parking=True)
    assert muted.log_history.query(component="FleetCoordinator") == []

    env = Environment(log_history_size=5000)
    _run_one_order(env, unreachable_parking=True)
    fleet_events = env.log_history.query(component="FleetCoordinator")
    assert [event.level for event in fleet_events] == ["ERROR", "WARNING"]
    assert "No path from" in fleet_events[0].message and "Repositioning failed" in fleet_events[1].message
