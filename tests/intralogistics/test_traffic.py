from __future__ import annotations

from types import SimpleNamespace

import pytest
import simpy

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.speed import TrapezoidalProfile
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
from tests.intralogistics.traffic_support import line


def _make_agv(env: Environment, node: Node | None = None) -> AGV:
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
        graph, nodes = line(4)
        env = Environment()
        agv = _make_agv(env, nodes["N0"])
        controller = FreeFlowController()
        controller.register_fleet(SimpleNamespace(graph=graph))  # type: ignore[arg-type]
        return controller, env, agv, nodes

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

    def test_view_graph_is_the_registered_fleets_and_the_first_wins(self) -> None:
        controller, _, _, _ = self._controller()
        graph = controller.view.graph
        controller.register_fleet(SimpleNamespace(graph=line(2)[0]))  # type: ignore[arg-type]
        assert controller.view.graph is graph

    def test_view_graph_requires_a_fleet(self) -> None:
        with pytest.raises(RuntimeError):
            FreeFlowController().view.graph

    def test_view_speed_limit_reads_the_arc(self) -> None:
        a, b = Node("A", 0, 0), Node("B", 10, 0)
        controller = FreeFlowController()
        controller.register_fleet(SimpleNamespace(graph=LayoutGraph([a, b], [Arc(a, b, speed_limit=1.5)])))  # type: ignore[arg-type]
        assert controller.view.speed_limit(a, b) == 1.5

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (AGVState.IDLE, Disposition.IDLE),
            (AGVState.STRANDED, Disposition.STRANDED),
            (AGVState.WAITING_LOAD, Disposition.SERVING),
            (AGVState.WAITING_UNLOAD, Disposition.SERVING),
            (AGVState.CHARGING, Disposition.SERVING),
            (AGVState.TRAVELING_EMPTY, Disposition.MOVING),
            (AGVState.TRAVELING_LOADED, Disposition.MOVING),
        ],
    )
    def test_view_disposition(self, state: AGVState, expected: Disposition) -> None:
        controller, _, agv, _ = self._controller()
        agv._state = state
        assert controller.view.disposition(agv) is expected
