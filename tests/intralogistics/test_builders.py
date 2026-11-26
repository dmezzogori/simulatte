from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.order import OrderStatus
from simulatte.intralogistics.sku import SKU

from tests.intralogistics.test_collectors import _declare_lines, _roles


# ---------------------------------------------------------------------------
# Part 1: Import surface test
# ---------------------------------------------------------------------------


class TestImportSurface:
    """Every public type is importable from simulatte.intralogistics."""

    def test_graph_exports(self) -> None:
        from simulatte.intralogistics import Arc, LayoutGraph, Node

        assert Node is not None
        assert Arc is not None
        assert LayoutGraph is not None

    def test_routing_exports(self) -> None:
        from simulatte.intralogistics import Route, Router, RouteRequest, ShortestDistanceRouter

        assert Router is not None
        assert Route is not None
        assert RouteRequest is not None
        assert ShortestDistanceRouter is not None

    def test_traffic_exports(self) -> None:
        from simulatte.intralogistics import (
            FreeFlowController,
            GiveUp,
            KeepWaiting,
            Reroute,
            ReservationController,
            StandardTrafficPolicy,
            StepAside,
            TrafficController,
            TrafficPolicy,
            TrafficView,
            Zone,
        )

        assert TrafficController is not None
        assert TrafficView is not None
        assert TrafficPolicy is not None
        assert FreeFlowController is not None
        assert ReservationController is not None
        assert StandardTrafficPolicy is not None
        assert (KeepWaiting, Reroute, StepAside, GiveUp) is not None
        assert Zone is not None

    def test_controller_protocol_exports(self) -> None:
        """What a third-party traffic controller needs (spec §14: ``Claim`` and ``ClaimRequest`` replace the old
        types)."""
        import simulatte.intralogistics as intralogistics
        from simulatte.intralogistics import network, traffic

        names = {
            "Claim": network,
            "ClaimItem": network,
            "Segment": network,
            "Corridor": network,
            "ClaimRequest": traffic,
            "WaitHooks": traffic,
            "Wait": traffic,
            "Blocker": traffic,
            "Disposition": traffic,
            "Granted": traffic,
            "Rerouted": traffic,
            "SteppedAside": traffic,
            "GaveUp": traffic,
        }
        for name, module in names.items():
            assert name in intralogistics.__all__, name
            assert getattr(intralogistics, name) is getattr(module, name), name

    def test_sku_export(self) -> None:
        from simulatte.intralogistics import SKU

        assert SKU is not None

    def test_agv_exports(self) -> None:
        from simulatte.intralogistics import AGV, AGVState, AGVType

        assert AGV is not None
        assert AGVType is not None
        assert AGVState is not None

    def test_speed_exports(self) -> None:
        from simulatte.intralogistics import Kinematics, SpeedProfile, TrapezoidalProfile

        assert SpeedProfile is not None
        assert TrapezoidalProfile is not None
        assert Kinematics is not None

    def test_battery_export(self) -> None:
        from simulatte.intralogistics import Battery

        assert Battery is not None

    def test_warehouse_export(self) -> None:
        from simulatte.intralogistics import Warehouse

        assert Warehouse is not None

    def test_charging_export(self) -> None:
        from simulatte.intralogistics import ChargingStation

        assert ChargingStation is not None

    def test_parking_export(self) -> None:
        from simulatte.intralogistics import ParkingArea

        assert ParkingArea is not None

    def test_order_exports(self) -> None:
        from simulatte.intralogistics import OrderStatus, TransferOrder

        assert OrderStatus is not None
        assert TransferOrder is not None

    def test_policies_exports(self) -> None:
        from simulatte.intralogistics import (
            DispatchStrategy,
            LoadRecoveryStrategy,
            NearestIdleStrategy,
            NearestParkingPolicy,
            ReorderPointPolicy,
            ReplenishmentPolicy,
            RepositioningContext,
            RepositioningPolicy,
            ResumeDelivery,
            ReturnToOrigin,
            RoundRobinStrategy,
            StayInPlace,
        )

        assert DispatchStrategy is not None
        assert NearestIdleStrategy is not None
        assert RoundRobinStrategy is not None
        assert ReplenishmentPolicy is not None
        assert ReorderPointPolicy is not None
        assert RepositioningPolicy is not None
        assert RepositioningContext is not None
        assert StayInPlace is not None
        assert NearestParkingPolicy is not None
        assert LoadRecoveryStrategy is not None
        assert ReturnToOrigin is not None
        assert ResumeDelivery is not None

    def test_fleet_export(self) -> None:
        from simulatte.intralogistics import FleetCoordinator

        assert FleetCoordinator is not None

    def test_metrics_exports(self) -> None:
        from simulatte.intralogistics import FleetKPIs, FleetTimeSeries, OrderEMACollector

        assert OrderEMACollector is not None
        assert FleetTimeSeries is not None
        assert FleetKPIs is not None

    def test_builder_export(self) -> None:
        from simulatte.intralogistics import build_simple_system

        assert build_simple_system is not None


# ---------------------------------------------------------------------------
# Part 2: build_simple_system creates a working coordinator
# ---------------------------------------------------------------------------


@pytest.fixture
def env() -> Environment:
    return Environment()


class TestBuildSimpleSystem:
    """build_simple_system() creates a functional intralogistics system."""

    def test_creates_working_coordinator(self, env: Environment) -> None:
        """Submit an order, run the simulation, and verify order completes."""
        from simulatte.intralogistics import build_simple_system

        coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env, layout=env.layout)

        # Default products
        default_sku_a = SKU("A", 1.0, 0.1)

        # Warehouse A should have inventory for default products
        level = wh_a.get_inventory_level(default_sku_a)
        assert level > 0

        # Create and submit an order
        order = coordinator.create_order(
            sku=default_sku_a,
            quantity=5,
            origin=wh_a,
            destination=wh_b,
        )
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED
        assert order.delivered_at is not None

    def test_return_types_and_defaults(self, env: Environment) -> None:
        """Verify the returned objects have expected structure and defaults."""
        from simulatte.intralogistics import (
            AGV,
            FleetCoordinator,
            LayoutGraph,
            Warehouse,
            build_simple_system,
        )
        from simulatte.layout import GraphRef

        coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env, layout=env.layout)

        assert isinstance(coordinator, FleetCoordinator)
        assert isinstance(agvs, list)
        assert all(isinstance(a, AGV) for a in agvs)
        assert isinstance(wh_a, Warehouse)
        assert isinstance(wh_b, Warehouse)
        assert isinstance(graph, GraphRef)  # the layout's graph; it resolves to a LayoutGraph
        assert isinstance(env.layout.prepare().network.graph, LayoutGraph)

        # Default n_agvs=2
        assert len(agvs) == 2

    def test_graph_has_five_nodes(self, env: Environment) -> None:
        """The graph should have 5 nodes in a linear layout."""
        from simulatte.intralogistics import build_simple_system

        build_simple_system(env, layout=env.layout)
        graph = env.layout.prepare().network.graph
        assert len(graph._nodes) == 5


# ---------------------------------------------------------------------------
# Part 3: Custom configuration
# ---------------------------------------------------------------------------


class TestCustomConfiguration:
    """Custom parameters are reflected in the built system."""

    def test_custom_agv_count(self, env: Environment) -> None:
        from simulatte.intralogistics import build_simple_system

        coordinator, agvs, _, _, _ = build_simple_system(env, n_agvs=4, layout=env.layout)
        assert len(agvs) == 4
        assert len(coordinator.fleet) == 4

    def test_custom_products(self, env: Environment) -> None:
        from simulatte.intralogistics import build_simple_system

        custom_products = [SKU("X", 3.0, 0.3), SKU("Y", 4.0, 0.4)]
        coordinator, agvs, wh_a, wh_b, _ = build_simple_system(env, products=custom_products, layout=env.layout)

        # Warehouses should know about the custom products
        for sku in custom_products:
            assert sku in wh_a.inventory
            assert sku in wh_b.inventory

    def test_custom_products_order_completes(self, env: Environment) -> None:
        """An order using custom products completes successfully."""
        from simulatte.intralogistics import build_simple_system

        custom_sku = SKU("CUSTOM", 2.0, 0.2)
        coordinator, agvs, wh_a, wh_b, _ = build_simple_system(env, products=[custom_sku], layout=env.layout)

        order = coordinator.create_order(
            sku=custom_sku,
            quantity=3,
            origin=wh_a,
            destination=wh_b,
        )
        coordinator.submit(order)
        env.run()

        assert order.status == OrderStatus.COMPLETED

    def test_custom_initial_inventory(self, env: Environment) -> None:
        from simulatte.intralogistics import build_simple_system

        sku_x = SKU("X", 1.0, 0.1)
        coordinator, _, wh_a, wh_b, _ = build_simple_system(
            env,
            products=[sku_x],
            initial_inventory_a={sku_x: 50},
            initial_inventory_b={sku_x: 25},
            layout=env.layout,
        )

        assert wh_a.get_inventory_level(sku_x) == 50
        assert wh_b.get_inventory_level(sku_x) == 25


# ---------------------------------------------------------------------------
# Part 4: Parking area included
# ---------------------------------------------------------------------------


class TestBuildSimpleSystemParkingArea:
    def test_coordinator_has_parking_areas(self) -> None:
        from simulatte.environment import Environment
        from simulatte.intralogistics.builders import build_simple_system

        env = Environment()
        coordinator, agvs, wh_a, wh_b, graph = build_simple_system(env, layout=env.layout)
        assert len(coordinator.parking_areas) > 0


# ---------------------------------------------------------------------------
# Part 5: Entity names and prefixes
# ---------------------------------------------------------------------------


class TestBuildSimpleSystemNames:
    def test_default_ids(self, env: Environment) -> None:
        from simulatte.intralogistics.builders import build_simple_system

        coordinator, agvs, wh_a, wh_b, _ = build_simple_system(env, layout=env.layout)
        assert [agv.id for agv in agvs] == ["vehicle-0", "vehicle-1"]  # named by the builder
        assert (coordinator.id, wh_a.id, wh_b.id) == ("fleet", "WH-A", "WH-B")
        graph = env.layout.prepare().network.graph
        assert [node.id for node in graph.nodes] == ["WH_A_OUT", "N1", "N2", "N3", "WH_B_IN"]
        assert (coordinator.charging_stations[0].id, coordinator.parking_areas[0].id) == ("CS-CENTER", "PA-N1")

    def test_prefixes_keep_two_systems_apart(self, env: Environment) -> None:
        from simulatte.intralogistics.builders import build_simple_system

        _declare_lines(env, "a.", "b.")  # one network for the two systems
        a = build_simple_system(env, prefix="a.", layout=env.layout, nodes=_roles(env, "a."))
        b = build_simple_system(env, prefix="b.", n_agvs=1, layout=env.layout, nodes=_roles(env, "b."))
        assert [agv.id for agv in a[1]] == ["a.agv-0", "a.agv-1"]
        assert [agv.id for agv in b[1]] == ["b.agv-0"]
        assert (a[0].id, b[0].id, b[2].id) == ("a.fleet", "b.fleet", "b.WH-A")
        graph = env.layout.prepare().network.graph
        assert [node.id for node in graph.nodes if node.id.startswith("b.")] == [
            "b.WH_A_OUT",
            "b.N1",
            "b.N2",
            "b.N3",
            "b.WH_B_IN",
        ]
        # system b stands on its own nodes
        endpoints = (b[2].output_bays[0], b[1][0].current_node, b[0].charging_stations[0].node, b[3].input_bays[0])
        assert [node.id for node in endpoints] == ["b.WH_A_OUT", "b.N1", "b.N2", "b.WH_B_IN"]
        with pytest.raises(ValueError, match="already used"):
            build_simple_system(env, prefix="a.", layout=env.layout, nodes=_roles(env, "a."))

        order = b[0].create_order(sku=next(iter(b[2].inventory)), quantity=1, origin=b[2], destination=b[3])
        b[0].submit(order)
        env.run()
        assert order.status is OrderStatus.COMPLETED and order.assigned_agv is b[1][0]


def test_builder_passes_traffic_controller_router_and_length_through(env: Environment) -> None:
    from simulatte.intralogistics import FreeFlowController, ShortestDistanceRouter, build_simple_system

    controller, router = FreeFlowController(), ShortestDistanceRouter("astar")
    fleet, agvs, _, _, _ = build_simple_system(
        env, traffic_controller=controller, router=router, agv_length=1.5, layout=env.layout
    )
    assert fleet.traffic_controller is controller and fleet.router is router
    graph = env.layout.prepare().network.graph
    assert controller.view.graph is graph
    assert all(agv.agv_type.length == 1.5 for agv in agvs)
    other = Environment()
    default, defaults, _, _, _ = build_simple_system(other, layout=other.layout)
    assert isinstance(default.traffic_controller, FreeFlowController)
    assert isinstance(default.router, ShortestDistanceRouter) and default.router.algorithm == "dijkstra"
    assert defaults[0].agv_type.length is None


def test_builder_digest():
    """The digest of a run of the builder's model on the environment's layout, its AGVs named ``vehicle-<i>``; pinned
    again for the vehicle's ``wait`` and ``route`` (studio map spec §6.2), then for the capacities and the traffic
    counters (§6.4, §6.6), each time the only change to the run's projection."""
    from simulatte.intralogistics import build_simple_system

    with Environment(seed=17) as env:
        env.enable_digest()
        fleet, agvs, origin, destination, _ = build_simple_system(env, layout=env.layout)
        assert all(env.entities.is_named(agv.id) for agv in agvs)  # the builder names them vehicle-<i>
        for quantity in (5, 8, 12, 6):
            fleet.submit(
                fleet.create_order(
                    sku=next(iter(origin.inventory)), quantity=quantity, origin=origin, destination=destination
                )
            )
        env.run(until=120)
        assert env.fingerprint().digest == "220fffc3a62e87327985f662c9036f755235c1129ab6308498f4964de76bf0a4"
