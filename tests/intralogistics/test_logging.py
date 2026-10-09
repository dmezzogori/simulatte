from __future__ import annotations

from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.events import DomainEvent
from simulatte.intralogistics.agv import AGV
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.events import AgvMoveStarted, AgvStateChanged, OrderStatusChanged
from simulatte.intralogistics.graph import Node
from simulatte.logger import SimLogger


@pytest.fixture
def _debug_level():
    """Temporarily set the global log level to DEBUG so env.debug() calls
    are recorded in the history buffer."""
    original = SimLogger.get_level()
    SimLogger.set_level("DEBUG")
    yield
    SimLogger.set_level(original)


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


@pytest.mark.usefixtures("_debug_level")
def test_fleet_and_agv_transitions_are_events() -> None:
    """FleetCoordinator and AGV transitions are domain events, not debug logs (spec §7.2); the Warehouse still
    logs at DEBUG."""
    env = Environment(log_history_size=5000)
    seen: list[DomainEvent] = []
    env.bus.subscribe(seen.append, (OrderStatusChanged, AgvStateChanged, AgvMoveStarted))
    _run_one_order(env)

    assert {event.type_name for event in seen} == {"order.status_changed", "agv.state_changed", "agv.move_started"}
    assert [e.status for e in seen if isinstance(e, OrderStatusChanged)][-1] == "COMPLETED"
    assert env.log_history.query(component="FleetCoordinator") == []
    assert env.log_history.query(component="AGV") == []
    assert len(env.log_history.query(component="Warehouse")) > 0, "Expected at least one Warehouse event"


@pytest.mark.usefixtures("_debug_level")
def test_disable_component_suppresses_its_logs() -> None:
    """``env.logger.disable_component("Warehouse")`` suppresses Warehouse logs while the fleet's errors and
    warnings are still recorded."""
    env = Environment(log_history_size=5000)
    env.logger.disable_component("Warehouse")
    _run_one_order(env, unreachable_parking=True)

    fleet_events = env.log_history.query(component="FleetCoordinator")
    assert len(env.log_history.query(component="Warehouse")) == 0, "Warehouse events should be suppressed"
    assert [event.level for event in fleet_events] == ["ERROR", "WARNING"]
    assert "No path from" in fleet_events[0].message and "Repositioning failed" in fleet_events[1].message
