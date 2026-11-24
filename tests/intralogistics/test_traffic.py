from __future__ import annotations

from types import SimpleNamespace

import pytest
import simpy

from simulatte._wire import freeze
from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.layout import NodeRef
from simulatte.intralogistics.network import Claim
from simulatte.intralogistics.traffic import (
    ClaimRequest,
    Disposition,
    FreeFlowController,
    Granted,
    Rerouted,
    TrafficController,
    TrafficView,
    WaitHooks,
)
from tests.intralogistics.traffic_support import declare, line


def _make_agv(env: Environment, node: NodeRef | None = None) -> AGV:
    profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
    agv_type = AGVType(
        name="test",
        speed_profile=profile,
        battery_capacity=100.0,
        weight_capacity=500.0,
        volume_capacity=2.0,
    )
    return AGV(env=env, agv_type=agv_type, initial_node=node)


class TestFreeFlowController:
    def _controller(self) -> tuple[FreeFlowController, Environment, AGV, dict[str, Node]]:
        env = Environment()
        world = line(env, 4)
        agv = _make_agv(env, world.ref("N0"))
        controller = FreeFlowController()
        controller.register_fleet(SimpleNamespace(graph=world.canonical_graph()))  # type: ignore[arg-type]
        return controller, env, agv, {f"N{i}": world.node(f"N{i}") for i in range(4)}

    def test_satisfies_the_protocols(self) -> None:
        controller = self._controller()[0]
        assert isinstance(controller, TrafficController)
        assert isinstance(controller.view, TrafficView)
        assert controller.controls_occupancy is False

    def test_next_claim_covers_the_rest_of_the_route(self) -> None:
        controller, _, agv, nodes = self._controller()
        route = [nodes[f"N{i}"] for i in range(4)]
        assert controller.next_claim(agv, route, 1) == Claim((), 2)

    def test_request_is_granted_at_once(self) -> None:
        controller, _, agv, _ = self._controller()
        hooks = WaitHooks(
            route=lambda: (), loaded=lambda: False, resolutions=lambda: 0,
            try_reroute=lambda avoid, ratio: False, try_step_aside=lambda target: False, give_up=lambda: False,
        )  # fmt: skip
        claim = Claim((), 1)
        request = controller.request(agv, claim, hooks)
        assert request.agv is agv
        assert request.claim is claim
        assert request.hooks is hooks
        assert request.event.triggered
        assert request.event.value == Granted()
        assert request.granted is True

    def test_request_not_granted_until_event_succeeds(self) -> None:
        _, env, agv, _ = self._controller()
        hooks = None  # type: ignore[assignment]
        pending_request = ClaimRequest(agv, Claim((), 1), hooks, simpy.Event(env))
        assert pending_request.granted is False
        pending_request.event.succeed(Rerouted())
        assert pending_request.granted is False

    def test_lifecycle_calls_do_nothing(self) -> None:
        controller, env, agv, nodes = self._controller()
        seen: list[object] = []
        env.bus.subscribe(seen.append, "**")
        before = dict(env.entities._live)
        request = controller.request(agv, Claim((), 1), None)  # type: ignore[arg-type]
        controller.place(agv, nodes["N0"])
        controller.stopped(request)
        controller.withdraw(request)
        controller.release(agv, [nodes["N1"]])
        controller.retain(agv, [nodes["N1"]])
        assert seen == []
        assert env.entities._live == before

    def test_view_reports_an_empty_network(self) -> None:
        controller, _, agv, nodes = self._controller()
        view = controller.view
        n0, n1 = nodes["N0"], nodes["N1"]
        assert view.length(n0, n1) == 10.0
        assert view.speed_limit(n0, n1) is None
        assert view.capacity(n0) == 1
        assert view.resources(n0, n1) == ()
        assert view.holders(n0) == ()
        assert view.pending(n0) == 0
        assert view.route_of(agv) == ()
        assert view.admissible(agv, Claim((), 1)) is True
        assert view.mean_hold(n0) == 0.0
        assert view.blockers(agv, n0, n1) == ()
        assert view.branch_root(n0) is False

    def test_view_graph_is_the_registered_fleets_and_fleets_on_it_share(self) -> None:
        controller, _, _, _ = self._controller()
        graph = controller.view.graph
        controller.register_fleet(SimpleNamespace(graph=graph))  # type: ignore[arg-type]
        assert controller.view.graph is graph

    def test_a_fleet_on_another_graph_is_refused(self) -> None:
        controller, _, _, _ = self._controller()
        graph = controller.view.graph
        with pytest.raises(ValueError, match="each graph needs its own FreeFlowController"):
            controller.register_fleet(SimpleNamespace(graph=line(Environment(), 2).canonical_graph()))  # type: ignore[arg-type]
        assert controller.view.graph is graph

    def test_two_built_systems_cannot_share_one_controller(self) -> None:
        from simulatte.intralogistics import build_simple_system

        env = Environment()
        controller = FreeFlowController()
        build_simple_system(env, prefix="a.", traffic_controller=controller)
        with pytest.raises(ValueError, match="each graph needs its own FreeFlowController"):
            build_simple_system(env, prefix="b.", traffic_controller=controller)

    def test_two_fleets_on_one_graph_share_one_controller(self) -> None:
        from simulatte.intralogistics.fleet import FleetCoordinator

        env = Environment()
        world = line(env, 3)
        controller = FreeFlowController()
        fleets = [
            FleetCoordinator(
                env=env,
                graph=world.graph,
                fleet=[_make_agv(env, world.ref(start))],
                warehouses=[],
                charging_stations=[],
                traffic_controller=controller,
            )
            for start in ("N0", "N2")
        ]
        processes = [
            env.process(fleet._executor.travel(fleet.fleet[0], world.node(goal), loaded=False))
            for fleet, goal in zip(fleets, ("N1", "N1"))
        ]
        env.run()
        assert all(process.value.name == "ARRIVED" for process in processes)

    def test_a_layout_fleet_registers_with_its_pending_graph(self) -> None:
        from simulatte import Layout
        from simulatte.intralogistics.fleet import FleetCoordinator

        env = Environment()
        layout = Layout(env)
        world = line(env, 2)
        controller = FreeFlowController()
        for name in ("a", "b"):  # both graphs are still references to the layout's graph: one graph
            FleetCoordinator(
                env=env,
                graph=layout.graph(),
                fleet=[],
                warehouses=[],
                charging_stations=[],
                traffic_controller=controller,
                name=name,
            )
        layout.prepare()
        assert controller.view.graph is world.canonical_graph()

    def test_view_graph_requires_a_fleet(self) -> None:
        with pytest.raises(RuntimeError):
            FreeFlowController().view.graph

    def test_view_speed_limit_reads_the_arc(self) -> None:
        a, b = Node("A", 0, 0), Node("B", 10, 0)
        world = declare(Environment(), LayoutGraph([a, b], [Arc(a, b, speed_limit=1.5)]))
        controller = FreeFlowController()
        controller.register_fleet(SimpleNamespace(graph=world.canonical_graph()))  # type: ignore[arg-type]
        assert controller.view.speed_limit(world.node("A"), world.node("B")) == 1.5

    @pytest.mark.parametrize(
        ("state", "moving", "expected"),
        [
            (AGVState.IDLE, False, Disposition.IDLE),
            (AGVState.STRANDED, False, Disposition.STRANDED),
            (AGVState.WAITING_LOAD, False, Disposition.SERVING),
            (AGVState.WAITING_UNLOAD, False, Disposition.SERVING),
            (AGVState.CHARGING, False, Disposition.SERVING),
            (AGVState.TRAVELING_EMPTY, True, Disposition.MOVING),
            (AGVState.TRAVELING_LOADED, True, Disposition.MOVING),
            # travelling at rest between two arcs and not in a traffic wait: turning (spec §9.4)
            (AGVState.TRAVELING_EMPTY, False, Disposition.SERVING),
            (AGVState.TRAVELING_LOADED, False, Disposition.SERVING),
        ],
    )
    def test_view_disposition(self, state: AGVState, moving: bool, expected: Disposition) -> None:
        controller, _, agv, _ = self._controller()
        agv._state = state
        agv.motion = freeze({"from": "N0", "to": "N1", "t_start": 0.0, "t_end": 1.0}) if moving else None
        assert controller.view.disposition(agv) is expected
