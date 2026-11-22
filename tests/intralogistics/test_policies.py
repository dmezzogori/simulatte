from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics import DispatchContext, FastestRouter
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.order import OrderStatus, TransferOrder
from simulatte.intralogistics.parking import ParkingArea
from simulatte.intralogistics.policies import (
    DispatchStrategy,
    LoadRecoveryStrategy,
    NearestIdleStrategy,
    NearestParkingPolicy,
    ReorderPointPolicy,
    RepositioningContext,
    RepositioningPolicy,
    ReplenishmentPolicy,
    ResumeDelivery,
    ReturnToOrigin,
    RoundRobinStrategy,
    StayInPlace,
)
from simulatte.intralogistics.routing import Route, Router, RouteRequest, ShortestDistanceRouter
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import TrafficView
from simulatte.intralogistics.warehouse import Warehouse
from tests.intralogistics.test_routing import free_view


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def env() -> Environment:
    return Environment()


@pytest.fixture
def speed_profile() -> TrapezoidalProfile:
    return TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)


@pytest.fixture
def sku_a() -> SKU:
    return SKU(id="A", weight=1.0, volume=0.01)


def _make_agv(
    env: Environment,
    speed_profile: TrapezoidalProfile,
    agv_id: str,
    node: Node,
    *,
    state: AGVState = AGVState.IDLE,
    weight_capacity: float = 100.0,
    volume_capacity: float = 10.0,
) -> AGV:
    agv_type = AGVType(
        name="test",
        speed_profile=speed_profile,
        battery_capacity=100.0,
        weight_capacity=weight_capacity,
        volume_capacity=volume_capacity,
    )
    agv = AGV(env=env, agv_type=agv_type, agv_id=agv_id, initial_node=node)
    agv.transition_to(state)
    return agv


def _make_warehouse(
    env: Environment,
    name: str,
    input_bays: list[Node],
    output_bays: list[Node],
    products: list[SKU],
    initial_inventory: dict[SKU, int] | None = None,
) -> Warehouse:
    return Warehouse(
        env=env,
        name=name,
        input_bays=input_bays,
        output_bays=output_bays,
        n_slots=5,
        products=products,
        initial_inventory=initial_inventory,
        pick_time=1.0,
        put_time=1.0,
    )


def _make_linear_graph() -> tuple[list[Node], LayoutGraph]:
    """Create a simple linear graph:  N0 -- N1 -- N2 -- N3  (10 units apart)."""
    nodes = [
        Node(id="N0", x=0.0, y=0.0),
        Node(id="N1", x=10.0, y=0.0),
        Node(id="N2", x=20.0, y=0.0),
        Node(id="N3", x=30.0, y=0.0),
    ]
    arcs = [
        Arc(source=nodes[0], target=nodes[1]),
        Arc(source=nodes[1], target=nodes[2]),
        Arc(source=nodes[2], target=nodes[3]),
    ]
    return nodes, LayoutGraph(nodes, arcs)


def _context(graph: LayoutGraph, router: Router | None = None) -> DispatchContext:
    """A dispatch context on `graph` without traffic, with `router` (default ``ShortestDistanceRouter``)."""
    return DispatchContext(graph, router or ShortestDistanceRouter(), free_view(graph))


def _repositioning(
    graph: LayoutGraph, parking_areas: Sequence[ParkingArea], fleet: Sequence[AGV], router: Router | None = None
) -> RepositioningContext:
    """A repositioning context on `graph` without traffic and charging stations, with `router` (default
    ``ShortestDistanceRouter``)."""
    return RepositioningContext(
        graph=graph,
        parking_areas=parking_areas,
        charging_stations=[],
        fleet=fleet,
        router=router or ShortestDistanceRouter(),
        view=free_view(graph),
    )


class Spy:
    """Answers as ``ShortestDistanceRouter`` and records every request."""

    def __init__(self) -> None:
        self.requests: list[RouteRequest] = []

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        self.requests.append(request)
        return ShortestDistanceRouter().route(request, view)


class Costing:
    """Answers the shortest path with the cost given in `costs` for ``"<origin id>><destination id>"``, or no route for
    a pair not in it."""

    def __init__(self, costs: dict[str, float]) -> None:
        self.costs = costs

    def route(self, request: RouteRequest, view: TrafficView) -> Route | None:
        cost = self.costs.get(f"{request.origin.id}>{request.destination.id}")
        if cost is None:
            return None
        found = ShortestDistanceRouter().route(request, view)
        assert found is not None
        return Route(found.nodes, cost)


def _slow_arm(env: Environment, speed_profile: TrapezoidalProfile) -> tuple[dict[str, Node], LayoutGraph]:
    """``S`` reaches ``E`` in 20 m through ``P`` (limited to 0.5) or in 30 m through ``Q``; ``R`` reaches ``E`` in 25 m
    without a limit."""
    nodes = {n: Node(n, x, y) for n, x, y in [("S", 0, 0), ("P", 10, 0), ("Q", 10, 10), ("E", 20, 0), ("R", 20, 25)]}
    s, p, q, e, r = (nodes[n] for n in "SPQER")
    arcs = [
        Arc(s, p, length=10.0, speed_limit=0.5),
        Arc(p, e, length=10.0, speed_limit=0.5),
        Arc(s, q, length=15.0),
        Arc(q, e, length=15.0),
        Arc(r, e),
    ]
    return nodes, LayoutGraph(nodes.values(), arcs)


# ── Contexts ──────────────────────────────────────────────────────────


class TestContexts:
    def test_dispatch_cost_asks_the_router_for_the_travel_from_the_agvs_node(
        self, env: Environment, speed_profile: TrapezoidalProfile
    ) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "agv", nodes[1])
        spy = Spy()
        context = _context(graph, spy)
        assert context.cost(agv, nodes[3]) == 20.0
        assert context.cost(agv, nodes[0], load_weight=7.5) == 10.0
        assert [(r.agv, r.origin, r.destination, r.load_weight, r.avoid, r.purpose) for r in spy.requests] == [
            (agv, nodes[1], nodes[3], 0.0, frozenset(), "travel"),
            (agv, nodes[1], nodes[0], 7.5, frozenset(), "travel"),
        ]

    def test_repositioning_cost_asks_the_router_for_an_unloaded_travel(
        self, env: Environment, speed_profile: TrapezoidalProfile
    ) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "agv", nodes[0])
        spy = Spy()
        context = _repositioning(graph, [], [agv], spy)
        assert context.cost(agv, nodes[2]) == 20.0
        assert [(r.agv, r.origin, r.destination, r.load_weight, r.purpose) for r in spy.requests] == [
            (agv, nodes[0], nodes[2], 0.0, "travel")
        ]

    @pytest.mark.parametrize("kind", ["dispatch", "repositioning"])
    def test_cost_is_infinite_without_a_route_or_a_node(
        self, env: Environment, speed_profile: TrapezoidalProfile, kind: str
    ) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "agv", nodes[0])
        router = Costing({"N0>N1": 3.0})
        context = _context(graph, router) if kind == "dispatch" else _repositioning(graph, [], [agv], router)
        assert context.cost(agv, nodes[1]) == 3.0
        assert context.cost(agv, nodes[2]) == math.inf
        agv.current_node = None
        assert context.cost(agv, nodes[1]) == math.inf

    @pytest.mark.parametrize("kind", ["dispatch", "repositioning"])
    @pytest.mark.parametrize("cost", [-1.0, math.nan, math.inf])
    def test_invalid_route_cost_raises(
        self, env: Environment, speed_profile: TrapezoidalProfile, kind: str, cost: float
    ) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "agv", nodes[0])
        router = Costing({"N0>N2": cost})
        context = _context(graph, router) if kind == "dispatch" else _repositioning(graph, [], [agv], router)
        with pytest.raises(ValueError, match=rf"Costing returned a route of cost {cost} from N0 to N2"):
            context.cost(agv, nodes[2])

    def test_contexts_are_frozen(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        nodes, graph = _make_linear_graph()
        with pytest.raises(AttributeError):
            _context(graph).router = FastestRouter()  # ty: ignore[invalid-assignment]
        with pytest.raises(AttributeError):
            _repositioning(graph, [], []).router = FastestRouter()  # ty: ignore[invalid-assignment]


# ── DispatchStrategy: NearestIdleStrategy ─────────────────────────────


class TestNearestIdleStrategy:
    def test_selects_closest_idle_compatible_agv(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        # Warehouse origin with output bay at N0
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        # AGV close (at N1, distance 10) and AGV far (at N3, distance 30)
        agv_close = _make_agv(env, speed_profile, "agv-close", nodes[1])
        agv_far = _make_agv(env, speed_profile, "agv-far", nodes[3])

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        selected = strategy.select(order, [agv_far, agv_close], _context(graph))
        assert selected is agv_close

    def test_returns_none_when_no_idle_agv(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        agv = _make_agv(env, speed_profile, "agv-busy", nodes[1], state=AGVState.TRAVELING_LOADED)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        assert strategy.select(order, [agv], _context(graph)) is None

    def test_tie_break_by_agv_id(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        # Both at same distance (N1)
        agv_b = _make_agv(env, speed_profile, "b-agv", nodes[1])
        agv_a = _make_agv(env, speed_profile, "a-agv", nodes[1])

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        selected = strategy.select(order, [agv_b, agv_a], _context(graph))
        assert selected is agv_a  # "a-agv" < "b-agv" lexicographically

    def test_uses_per_agv_nearest_output_bay(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        node_a = Node(id="A", x=0.0, y=0.0)
        node_a_bay = Node(id="A_BAY", x=0.0, y=1.0)
        node_far = Node(id="FAR", x=100.0, y=0.0)
        node_far_bay = Node(id="FAR_BAY", x=100.0, y=1.0)
        node_dest = Node(id="DEST", x=50.0, y=50.0)
        graph = LayoutGraph(
            [node_a, node_a_bay, node_far, node_far_bay, node_dest],
            [
                Arc(source=node_a, target=node_a_bay),
                Arc(source=node_far, target=node_far_bay),
                Arc(source=node_a_bay, target=node_dest),
                Arc(source=node_far_bay, target=node_dest),
                Arc(source=node_a, target=node_far),
            ],
        )
        wh = _make_warehouse(env, "WH", [node_dest], [node_a_bay, node_far_bay], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [node_dest], [node_dest], [sku_a])

        agv_near_a = _make_agv(env, speed_profile, "agv-a", node_a)
        agv_at_far_bay = _make_agv(env, speed_profile, "agv-b", node_far_bay)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        selected = strategy.select(order, [agv_near_a, agv_at_far_bay], _context(graph))
        assert selected is agv_at_far_bay

    def test_returns_none_when_no_compatible_agv(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        # AGV with too-small capacity
        agv = _make_agv(env, speed_profile, "agv-weak", nodes[1], weight_capacity=0.1)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        assert strategy.select(order, [agv], _context(graph)) is None

    def test_unreachable_agv_gets_inf_distance(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        """An idle AGV on a disconnected node gets inf distance.
        When the only candidate is unreachable, select() returns None."""
        # Disconnected graph: N0 alone, N1 alone
        n0 = Node(id="N0", x=0.0, y=0.0)
        n1 = Node(id="N1", x=10.0, y=0.0)
        graph = LayoutGraph([n0, n1], [])

        wh = _make_warehouse(env, "WH", [n0], [n0], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [n1], [n1], [sku_a])

        # AGV at n1 which is disconnected from n0 (output bay)
        agv = _make_agv(env, speed_profile, "agv-disconnected", n1)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = NearestIdleStrategy()
        selected = strategy.select(order, [agv], _context(graph))
        # Only candidate is unreachable, so select() returns None
        assert selected is None

    def test_returns_none_when_all_unreachable(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        """M6: When all candidates have infinite distance, select() must return None."""
        island = Node(id="ISLAND", x=0.0, y=0.0)
        mainland = Node(id="MAINLAND", x=100.0, y=0.0)
        graph = LayoutGraph([island, mainland], [])  # disconnected

        wh = _make_warehouse(env, "WH", [mainland], [mainland], [sku_a])
        agv = _make_agv(env, speed_profile, "AGV-1", island)

        order = TransferOrder(
            sku=sku_a,
            quantity=1,
            origin=wh,
            destination=wh,
            created_at=0.0,
        )

        strategy = NearestIdleStrategy()
        result = strategy.select(order, [agv], _context(graph))
        assert result is None

    def test_picks_the_lowest_router_cost_to_the_best_output_bay(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0], nodes[3]], [sku_a])
        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh, created_at=0.0)
        agv_a = _make_agv(env, speed_profile, "agv-a", nodes[1])
        agv_b = _make_agv(env, speed_profile, "agv-b", nodes[2])
        costs = {"N1>N0": 8.0, "N1>N3": 6.0, "N2>N0": 7.0}  # no route from N2 to N3
        assert NearestIdleStrategy().select(order, [agv_b, agv_a], _context(graph, Costing(costs))) is agv_a
        del costs["N1>N3"]
        assert NearestIdleStrategy().select(order, [agv_b, agv_a], _context(graph, Costing(costs))) is agv_b
        assert NearestIdleStrategy().select(order, [agv_b, agv_a], _context(graph, Costing({}))) is None

    def test_the_fastest_router_picks_another_agv_than_the_default_one_past_a_slow_arm(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _slow_arm(env, speed_profile)
        wh = _make_warehouse(env, "WH", [nodes["E"]], [nodes["E"]], [sku_a])
        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh, created_at=0.0)
        near = _make_agv(env, speed_profile, "agv-a", nodes["S"])  # 20 m, but 40 s through P or 15 s through Q
        far = _make_agv(env, speed_profile, "agv-b", nodes["R"])  # 25 m, 12.5 s
        assert NearestIdleStrategy().select(order, [near, far], _context(graph)) is near
        assert NearestIdleStrategy().select(order, [near, far], _context(graph, FastestRouter())) is far

    def test_the_fastest_router_ranks_an_agv_by_its_battery_level_now(self, env: Environment, sku_a: SKU) -> None:
        """The drained AGV is 20 m from the bay with idle power 0.1 after 800 s idle (100 accounted, 20 now: 50 s at
        0.4 m/s); the fresh one is 30 m away at 2 m/s (15 s). The fresh one is chosen."""
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh, created_at=0.0)
        profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
        kinds = {
            power: AGVType(
                name=f"p{power}",
                speed_profile=profile,
                battery_capacity=100.0,
                weight_capacity=100.0,
                volume_capacity=10.0,
                idle_power=power,
            )
            for power in (0.1, 0.0)
        }
        drained = AGV(env=env, agv_type=kinds[0.1], agv_id="agv-a", initial_node=nodes[2])
        fresh = AGV(env=env, agv_type=kinds[0.0], agv_id="agv-b", initial_node=nodes[3])
        env.run(until=800.0)
        context = _context(graph, FastestRouter())
        assert context.cost(drained, nodes[0]) == pytest.approx(50.0, rel=1e-12)
        assert context.cost(fresh, nodes[0]) == pytest.approx(15.0, rel=1e-12)
        assert NearestIdleStrategy().select(order, [drained, fresh], context) is fresh
        assert NearestIdleStrategy().select(order, [drained, fresh], _context(graph)) is drained


# ── DispatchStrategy: RoundRobinStrategy ──────────────────────────────


class TestRoundRobinStrategy:
    def test_cycles_through_agvs(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        agv1 = _make_agv(env, speed_profile, "AGV-1", nodes[0])
        agv2 = _make_agv(env, speed_profile, "AGV-2", nodes[1])
        agv3 = _make_agv(env, speed_profile, "AGV-3", nodes[2])

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)
        fleet = [agv1, agv2, agv3]

        strategy = RoundRobinStrategy()
        first = strategy.select(order, fleet, _context(graph))
        second = strategy.select(order, fleet, _context(graph))
        third = strategy.select(order, fleet, _context(graph))
        fourth = strategy.select(order, fleet, _context(graph))

        assert first is agv1
        assert second is agv2
        assert third is agv3
        assert fourth is agv1  # wraps around

    def test_skips_non_idle_and_incompatible(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        agv_busy = _make_agv(env, speed_profile, "agv-busy", nodes[0], state=AGVState.TRAVELING_LOADED)
        agv_weak = _make_agv(env, speed_profile, "agv-weak", nodes[1], weight_capacity=0.1)
        agv_ok = _make_agv(env, speed_profile, "agv-ok", nodes[2])

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = RoundRobinStrategy()
        selected = strategy.select(order, [agv_busy, agv_weak, agv_ok], _context(graph))
        assert selected is agv_ok

    def test_returns_none_when_all_filtered(
        self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU
    ) -> None:
        nodes, graph = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_DEST", [nodes[3]], [nodes[3]], [sku_a])

        agv_busy = _make_agv(env, speed_profile, "agv-busy", nodes[0], state=AGVState.CHARGING)

        order = TransferOrder(sku=sku_a, quantity=1, origin=wh, destination=wh_dest, created_at=0.0)

        strategy = RoundRobinStrategy()
        assert strategy.select(order, [agv_busy], _context(graph)) is None


# ── RepositioningPolicy: StayInPlace ─────────────────────────────────


class TestStayInPlace:
    def test_returns_none(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "AGV-1", nodes[0])
        context = _repositioning(graph, [], [agv])
        policy = StayInPlace()
        assert policy.reposition(agv, context) is None


# ── RepositioningPolicy: NearestParkingPolicy ────────────────────────


class TestNearestParkingPolicy:
    def test_returns_nearest_parking_node_with_capacity(
        self, env: Environment, speed_profile: TrapezoidalProfile
    ) -> None:
        nodes, graph = _make_linear_graph()
        # AGV at N0, parking at N1 (10 units) and N3 (30 units)
        agv = _make_agv(env, speed_profile, "AGV-1", nodes[0])
        park_near = ParkingArea(env=env, name="P1", node=nodes[1], capacity=2)
        park_far = ParkingArea(env=env, name="P2", node=nodes[3], capacity=2)

        context = _repositioning(graph, [park_far, park_near], [agv])

        policy = NearestParkingPolicy()
        assert policy.reposition(agv, context) == nodes[1]

    def test_returns_none_when_all_full(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "AGV-1", nodes[0])
        park = ParkingArea(env=env, name="P1", node=nodes[1], capacity=1)

        # Fill the parking area by requesting the resource (auto-triggered
        # because capacity is available)
        park._resource.request()

        context = _repositioning(graph, [park], [agv])

        policy = NearestParkingPolicy()
        assert policy.reposition(agv, context) is None

    def test_returns_none_when_no_parking_areas(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        nodes, graph = _make_linear_graph()
        agv = _make_agv(env, speed_profile, "AGV-1", nodes[0])

        context = _repositioning(graph, [], [agv])

        policy = NearestParkingPolicy()
        assert policy.reposition(agv, context) is None

    def test_returns_none_when_current_node_is_none(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        """AGV with current_node=None -> return None immediately."""
        nodes, graph = _make_linear_graph()
        park = ParkingArea(env=env, name="P1", node=nodes[1], capacity=2)
        agv = _make_agv(env, speed_profile, "agv-no-node", nodes[0])
        agv.current_node = None  # Force None

        context = _repositioning(graph, [park], [agv])

        policy = NearestParkingPolicy()
        assert policy.reposition(agv, context) is None

    def test_parking_distance_calculation(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        """Test that distance is computed along graph path and nearest is chosen."""
        nodes, graph = _make_linear_graph()
        # AGV at N2 (20,0); parking at N1 (10,0) distance=10, N3 (30,0) distance=10
        agv = _make_agv(env, speed_profile, "AGV-1", nodes[2])
        park_1 = ParkingArea(env=env, name="P1", node=nodes[1], capacity=2)
        park_3 = ParkingArea(env=env, name="P3", node=nodes[3], capacity=2)

        context = _repositioning(graph, [park_1, park_3], [agv])

        policy = NearestParkingPolicy()
        result = policy.reposition(agv, context)
        # Both are equidistant: the first of the context's parking areas wins
        assert result == nodes[1]
        assert policy.reposition(agv, _repositioning(graph, [park_3, park_1], [agv])) == nodes[3]

    def test_ranks_areas_by_router_cost(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        nodes, graph = _slow_arm(env, speed_profile)
        agv = _make_agv(env, speed_profile, "AGV-1", nodes["S"])
        slow = ParkingArea(env=env, name="P-slow", node=nodes["P"], capacity=1)  # 10 m at 0.5: 20 s
        fast = ParkingArea(env=env, name="P-fast", node=nodes["Q"], capacity=1)  # 15 m at 2: 7.5 s
        policy = NearestParkingPolicy()
        assert policy.reposition(agv, _repositioning(graph, [slow, fast], [agv])) == nodes["P"]
        assert policy.reposition(agv, _repositioning(graph, [slow, fast], [agv], FastestRouter())) == nodes["Q"]
        costs = {"S>P": 4.0, "S>Q": 3.0}
        assert policy.reposition(agv, _repositioning(graph, [slow, fast], [agv], Costing(costs))) == nodes["Q"]

    def test_parking_unreachable_gets_inf_distance(self, env: Environment, speed_profile: TrapezoidalProfile) -> None:
        """Parking area on disconnected node -> inf distance, still picked if only option."""
        n0 = Node(id="N0", x=0.0, y=0.0)
        n_island = Node(id="ISLAND", x=100.0, y=100.0)
        graph = LayoutGraph([n0, n_island], [])  # No arcs

        agv = _make_agv(env, speed_profile, "AGV-1", n0)
        park = ParkingArea(env=env, name="P-ISLAND", node=n_island, capacity=2)

        context = _repositioning(graph, [park], [agv])

        policy = NearestParkingPolicy()
        # Only option is unreachable, but min still returns it
        result = policy.reposition(agv, context)
        assert result == n_island


# ── ReplenishmentPolicy: ReorderPointPolicy ──────────────────────────


class TestReorderPointPolicy:
    def test_triggers_order_below_threshold(self, env: Environment, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        # Monitored warehouse: low stock (5), threshold 10
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 5})
        # Source warehouse: high stock (100)
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 50},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[])

        assert len(orders) == 1
        order = orders[0]
        assert order.sku is sku_a
        assert order.quantity == 50
        assert order.origin is wh_high
        assert order.destination is wh_low
        assert order.status == OrderStatus.PENDING

    def test_no_duplicate_when_in_transit_exists(self, env: Environment, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 5})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        # Existing in-transit order for same sku to same destination
        existing = TransferOrder(
            sku=sku_a,
            quantity=50,
            origin=wh_high,
            destination=wh_low,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
        )

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 50},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[existing])
        assert len(orders) == 0

    def test_picks_source_with_highest_stock(self, env: Environment, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 5})
        wh_medium = _make_warehouse(env, "WH_MED", [nodes[1]], [nodes[1]], [sku_a], {sku_a: 50})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 200})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 30},
        )
        orders = policy.check(wh_low, [wh_low, wh_medium, wh_high], in_transit_orders=[])

        assert len(orders) == 1
        assert orders[0].origin is wh_high

    def test_sums_in_transit_quantities(self, env: Environment, sku_a: SKU) -> None:
        """S5: Policy sums in-transit quantities. With 45 on hand and 10
        in-transit (effective=55), threshold=50 should NOT trigger a new order."""
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 45})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        # In-transit order with 10 units heading to wh_low
        in_transit = TransferOrder(
            sku=sku_a,
            quantity=10,
            origin=wh_high,
            destination=wh_low,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
        )

        policy = ReorderPointPolicy(
            thresholds={sku_a: 50},
            reorder_quantity={sku_a: 30},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[in_transit])

        # effective_stock = 45 + 10 = 55 >= 50 → no new order
        assert len(orders) == 0

    def test_triggers_order_when_in_transit_insufficient(self, env: Environment, sku_a: SKU) -> None:
        """S5: When in-transit quantities are not enough, a new order is still created."""
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 30})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        # In-transit order with 10 units — effective = 30 + 10 = 40 < 50
        in_transit = TransferOrder(
            sku=sku_a,
            quantity=10,
            origin=wh_high,
            destination=wh_low,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
        )

        policy = ReorderPointPolicy(
            thresholds={sku_a: 50},
            reorder_quantity={sku_a: 30},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[in_transit])

        # effective_stock = 30 + 10 = 40 < 50 → new order
        assert len(orders) == 1
        assert orders[0].quantity == 30

    def test_excludes_completed_in_transit_from_sum(self, env: Environment, sku_a: SKU) -> None:
        """S5: Completed/failed/cancelled in-transit orders are not counted."""
        nodes, _ = _make_linear_graph()
        wh_low = _make_warehouse(env, "WH_LOW", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 45})
        wh_high = _make_warehouse(env, "WH_HIGH", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 100})

        # Completed in-transit order — should NOT be counted
        completed_order = TransferOrder(
            sku=sku_a,
            quantity=10,
            origin=wh_high,
            destination=wh_low,
            created_at=0.0,
            status=OrderStatus.COMPLETED,
        )

        policy = ReorderPointPolicy(
            thresholds={sku_a: 50},
            reorder_quantity={sku_a: 30},
        )
        orders = policy.check(wh_low, [wh_low, wh_high], in_transit_orders=[completed_order])

        # effective_stock = 45 + 0 = 45 < 50 → new order
        assert len(orders) == 1

    def test_no_other_warehouses_skips(self, env: Environment, sku_a: SKU) -> None:
        """When the only warehouse is the monitored one, no source exists -> skip."""
        nodes, _ = _make_linear_graph()
        wh_only = _make_warehouse(env, "WH_ONLY", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 5})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 50},
        )
        orders = policy.check(wh_only, [wh_only], in_transit_orders=[])

        # No other warehouses to source from -> no orders
        assert len(orders) == 0

    def test_in_transit_check_uses_equality_not_identity(self, env: Environment, sku_a: SKU) -> None:
        """L1: ReorderPointPolicy must use == for SKU comparison, not 'is'."""
        sku_a_copy = SKU(id=sku_a.id, weight=sku_a.weight, volume=sku_a.volume)
        assert sku_a == sku_a_copy
        assert sku_a is not sku_a_copy

        node = Node(id="N", x=0.0, y=0.0)
        wh_monitored = _make_warehouse(env, "WH-M", [node], [node], [sku_a], {sku_a: 5})
        wh_source = _make_warehouse(env, "WH-S", [node], [node], [sku_a], {sku_a: 100})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 20},
            reorder_quantity={sku_a: 15},
        )

        in_transit = TransferOrder(
            sku=sku_a_copy,
            quantity=15,
            origin=wh_source,
            destination=wh_monitored,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
        )

        orders = policy.check(wh_monitored, [wh_monitored, wh_source], [in_transit])
        assert len(orders) == 0

    def test_no_order_when_above_threshold(self, env: Environment, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh = _make_warehouse(env, "WH", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 100})
        wh_source = _make_warehouse(env, "WH_SRC", [nodes[3]], [nodes[3]], [sku_a], {sku_a: 200})

        policy = ReorderPointPolicy(
            thresholds={sku_a: 10},
            reorder_quantity={sku_a: 50},
        )
        orders = policy.check(wh, [wh, wh_source], in_transit_orders=[])
        assert len(orders) == 0


# ── LoadRecoveryStrategy: ReturnToOrigin ─────────────────────────────


class TestReturnToOrigin:
    def test_with_empty_load_skips_put(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        """When agv.current_load is falsy (None), the put loop is skipped."""
        nodes, _ = _make_linear_graph()
        wh_orig = _make_warehouse(env, "WH_O", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_D", [nodes[3]], [nodes[3]], [sku_a])
        agv = _make_agv(env, speed_profile, "AGV-1", nodes[1])
        agv.current_load = None

        order = TransferOrder(
            sku=sku_a,
            quantity=1,
            origin=wh_orig,
            destination=wh_dest,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
            assigned_agv=agv,
        )

        strategy = ReturnToOrigin()
        gen = strategy.recover(order, agv, None)  # ty: ignore[invalid-argument-type]
        list(gen)

        assert order.status == OrderStatus.PENDING
        assert order.assigned_agv is None

    def test_sets_pending_and_clears_agv(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh_orig = _make_warehouse(env, "WH_O", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_D", [nodes[3]], [nodes[3]], [sku_a])
        agv = _make_agv(env, speed_profile, "AGV-1", nodes[1])

        order = TransferOrder(
            sku=sku_a,
            quantity=1,
            origin=wh_orig,
            destination=wh_dest,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
            assigned_agv=agv,
        )

        strategy = ReturnToOrigin()
        gen = strategy.recover(order, agv, None)  # ty: ignore[invalid-argument-type]
        # Exhaust the generator
        list(gen)

        assert order.status == OrderStatus.PENDING
        assert order.assigned_agv is None

    def test_with_cargo_sets_pending(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        """ReturnToOrigin sets status to PENDING (physical return is coordinator's job)."""
        nodes, _ = _make_linear_graph()
        wh_orig = _make_warehouse(env, "WH_O", [nodes[0]], [nodes[0]], [sku_a], {sku_a: 100})
        wh_dest = _make_warehouse(env, "WH_D", [nodes[3]], [nodes[3]], [sku_a])
        agv = _make_agv(env, speed_profile, "AGV-1", nodes[1])
        agv.current_load = {sku_a: 10}

        order = TransferOrder(
            sku=sku_a,
            quantity=10,
            origin=wh_orig,
            destination=wh_dest,
            created_at=0.0,
            status=OrderStatus.IN_TRANSIT,
            assigned_agv=agv,
        )

        strategy = ReturnToOrigin()
        gen = strategy.recover(order, agv, None)  # ty: ignore[invalid-argument-type]
        list(gen)

        assert order.status == OrderStatus.PENDING
        assert order.assigned_agv is None
        # Cargo is NOT returned by the strategy — coordinator handles it
        assert agv.current_load == {sku_a: 10}


# ── LoadRecoveryStrategy: ResumeDelivery ─────────────────────────────


class TestResumeDelivery:
    def test_keeps_in_transit_status(self, env: Environment, speed_profile: TrapezoidalProfile, sku_a: SKU) -> None:
        nodes, _ = _make_linear_graph()
        wh_orig = _make_warehouse(env, "WH_O", [nodes[0]], [nodes[0]], [sku_a])
        wh_dest = _make_warehouse(env, "WH_D", [nodes[3]], [nodes[3]], [sku_a])
        agv = _make_agv(env, speed_profile, "AGV-1", nodes[1])

        order = TransferOrder(
            sku=sku_a,
            quantity=1,
            origin=wh_orig,
            destination=wh_dest,
            created_at=0.0,
            status=OrderStatus.DISPATCHED,
            assigned_agv=agv,
        )

        strategy = ResumeDelivery()
        gen = strategy.recover(order, agv, None)  # ty: ignore[invalid-argument-type]
        list(gen)

        assert order.status == OrderStatus.IN_TRANSIT
        assert order.assigned_agv is agv


# ── Protocol conformance ─────────────────────────────────────────────


class TestProtocolConformance:
    def test_nearest_idle_is_dispatch_strategy(self) -> None:
        assert isinstance(NearestIdleStrategy(), DispatchStrategy)

    def test_round_robin_is_dispatch_strategy(self) -> None:
        assert isinstance(RoundRobinStrategy(), DispatchStrategy)

    def test_stay_in_place_is_repositioning_policy(self) -> None:
        assert isinstance(StayInPlace(), RepositioningPolicy)

    def test_nearest_parking_is_repositioning_policy(self) -> None:
        assert isinstance(NearestParkingPolicy(), RepositioningPolicy)

    def test_reorder_point_is_replenishment_policy(self) -> None:
        policy = ReorderPointPolicy(thresholds={}, reorder_quantity={})
        assert isinstance(policy, ReplenishmentPolicy)

    def test_return_to_origin_is_load_recovery_strategy(self) -> None:
        assert isinstance(ReturnToOrigin(), LoadRecoveryStrategy)

    def test_resume_delivery_is_load_recovery_strategy(self) -> None:
        assert isinstance(ResumeDelivery(), LoadRecoveryStrategy)
