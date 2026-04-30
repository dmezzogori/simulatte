from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.builders import build_simple_system
from simulatte.logger import SimLogger


@pytest.fixture
def _debug_level():
    """Temporarily set the global log level to DEBUG so env.debug() calls
    are recorded in the history buffer."""
    original = SimLogger.get_level()
    SimLogger.set_level("DEBUG")
    yield
    SimLogger.set_level(original)


@pytest.mark.usefixtures("_debug_level")
def test_logging_components_present() -> None:
    """Run a simple simulation and verify that FleetCoordinator, Warehouse,
    and AGV all emit at least one log event."""
    env = Environment(log_history_size=5000)
    coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env, n_agvs=1)
    sku = list(wh_a.inventory.keys())[0]

    order = coordinator.create_order(
        sku=sku,
        quantity=1,
        origin=wh_a,
        destination=wh_b,
    )
    coordinator.submit(order)
    env.run()

    fleet_events = env.log_history.query(component="FleetCoordinator")
    warehouse_events = env.log_history.query(component="Warehouse")
    agv_events = env.log_history.query(component="AGV")

    assert len(fleet_events) > 0, "Expected at least one FleetCoordinator event"
    assert len(warehouse_events) > 0, "Expected at least one Warehouse event"
    assert len(agv_events) > 0, "Expected at least one AGV event"


@pytest.mark.usefixtures("_debug_level")
def test_disable_component_suppresses_agv_logs() -> None:
    """Verify that ``env.logger.disable_component("AGV")`` suppresses AGV
    log events while other component events are still recorded."""
    env = Environment(log_history_size=5000)
    env.logger.disable_component("AGV")

    coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env, n_agvs=1)
    sku = list(wh_a.inventory.keys())[0]

    order = coordinator.create_order(
        sku=sku,
        quantity=1,
        origin=wh_a,
        destination=wh_b,
    )
    coordinator.submit(order)
    env.run()

    agv_events = env.log_history.query(component="AGV")
    fleet_events = env.log_history.query(component="FleetCoordinator")
    warehouse_events = env.log_history.query(component="Warehouse")

    assert len(agv_events) == 0, "AGV events should be suppressed"
    assert len(fleet_events) > 0, "FleetCoordinator events should still appear"
    assert len(warehouse_events) > 0, "Warehouse events should still appear"
