"""Motion descriptions and the movement events of AGVs (spec §6.4 ``agv.move_*``, §6.5)."""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.events import DomainEvent
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.events import AgvMoveEnded, AgvMoveInterrupted, AgvMoveStarted
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.order import OrderStatus
from simulatte.intralogistics.speed import LINEAR_MOTION, TrapezoidalProfile, describe_motion
from simulatte.intralogistics.reservation import ReservationController
from simulatte.intralogistics.traffic import FreeFlowController
from simulatte.intralogistics.warehouse import Warehouse

from tests.intralogistics.test_entities import SKU_A, _agv_type, _line_graph
from tests.intralogistics.test_fleet_events import FleetReplay
from tests.intralogistics.test_travel import steady
from tests.intralogistics.traffic_support import FullReplay, StillFleet, TrafficInvariants, World, declare, line


def _warehouses(env: Environment, world: World) -> tuple[Warehouse, Warehouse]:
    """WH-A on OUT, stocked, and WH-B on IN, empty, as ``test_entities._warehouses`` builds them."""

    def make(name: str, bay: str, level: int) -> Warehouse:
        return Warehouse(
            env=env,
            name=name,
            input_bays=[world.ref(bay)],
            output_bays=[world.ref(bay)],
            n_slots=2,
            products=[SKU_A],
            initial_inventory={SKU_A: level},
            pick_time=1.0,
            put_time=1.0,
        )

    return make("WH-A", "OUT", 100), make("WH-B", "IN", 0)


def _system(env: Environment) -> tuple[FleetCoordinator, list[AGV], Warehouse, Warehouse, World]:
    """``test_entities._system`` through the layout: the line OUT - C1 - C2 - IN declared on the layout of `env`,
    WH-A at OUT and WH-B at IN, one AGV at C1."""
    world = declare(env, _line_graph()[1])
    wh_a, wh_b = _warehouses(env, world)
    agvs = [AGV(env=env, agv_type=_agv_type(), initial_node=world.ref("C1"))]
    coordinator = FleetCoordinator(
        env=env, graph=world.graph, fleet=agvs, warehouses=[wh_a, wh_b], charging_stations=[]
    )
    return coordinator, agvs, wh_a, wh_b, world


def agv_heading(nodes: Any, source: str, target: str) -> float:
    """The heading of the chord from `source` to `target` among `nodes`."""
    by_id = {node.id: node for node in nodes}
    a, b = by_id[source], by_id[target]
    return math.atan2(b.y - a.y, b.x - a.x)


def _position(description: Any, t: float) -> tuple[float, float, float]:
    """Position and speed at time `t` along a trapezoidal description, integrated phase by phase, and the time at
    which the curve stops."""
    v_max, accel, decel, distance = (description[k] for k in ("v_max", "accel", "decel", "distance"))
    v_peak = min(v_max, math.sqrt(2 * distance * accel * decel / (accel + decel)))
    t_accel, t_decel = v_peak / accel, v_peak / decel
    d_accel, d_decel = v_peak**2 / (2 * accel), v_peak**2 / (2 * decel)
    t_cruise = (distance - d_accel - d_decel) / v_peak
    duration = t_accel + t_cruise + t_decel
    if t <= t_accel:
        return accel * t**2 / 2, accel * t, duration
    if t <= t_accel + t_cruise:
        return d_accel + v_peak * (t - t_accel), v_peak, duration
    s = t - t_accel - t_cruise
    return d_accel + v_peak * t_cruise + v_peak * s - decel * s**2 / 2, v_peak - decel * s, duration


@pytest.mark.parametrize(
    ("profile", "distance", "load_weight", "battery_level", "speed_limit"),
    [
        (TrapezoidalProfile(2.0, 1.0, 1.0), 10.0, 0.0, 1.0, None),  # cruise phase
        (TrapezoidalProfile(2.0, 1.0, 1.5), 1.0, 0.0, 1.0, None),  # triangular: v_max not reached
        (TrapezoidalProfile(3.0, 0.5, 2.0), 25.0, 0.0, 0.8, 1.2),  # battery-scaled, speed-limited
        (TrapezoidalProfile(3.0, 1.0, 1.0, load_speed_factor_fn=lambda w: 1 / (1 + w)), 12.0, 3.0, 1.0, None),
    ],
)
def test_trapezoidal_motion_integrates_to_travel_time(
    profile: TrapezoidalProfile, distance: float, load_weight: float, battery_level: float, speed_limit: float | None
) -> None:
    travel_time = profile.travel_time(distance, load_weight, battery_level, speed_limit)
    description = profile.motion(distance, load_weight, battery_level, speed_limit)
    assert description["curve"] == "trapezoidal" and description["distance"] == distance

    position, speed, duration = _position(description, travel_time)
    assert abs(duration - travel_time) <= 1e-9
    assert abs(position - distance) <= 1e-9
    assert abs(speed) <= 1e-9
    samples = [_position(description, travel_time * i / 100)[0] for i in range(101)]
    assert samples == sorted(samples)  # never moves backwards


def test_motion_reuses_the_factors_of_travel_time() -> None:
    calls: list[str] = []
    profile = TrapezoidalProfile(
        2.0,
        1.0,
        1.0,
        battery_degradation_fn=lambda level: calls.append("battery") or level,
        load_speed_factor_fn=lambda weight: calls.append("load") or 1.0,
    )
    profile.travel_time(10.0, 0.0, 0.5)
    assert calls == ["battery", "load"]
    assert profile.motion(10.0, 0.0, 0.5) == {
        "curve": "trapezoidal",
        "v_max": 1.0,
        "accel": 0.5,
        "decel": 1.0,
        "distance": 10.0,
    }
    assert calls == ["battery", "load"]  # same segment: no second call of the user functions
    profile.motion(10.0, 0.0, 0.25)  # other arguments: computed afresh
    assert calls == ["battery", "load", "battery", "load"]


def test_motion_edge_cases() -> None:
    profile = TrapezoidalProfile(2.0, 1.0, 1.0, battery_degradation_fn=lambda level: 0.0)
    assert profile.motion(0.0) == {"curve": "trapezoidal", "v_max": 2.0, "accel": 1.0, "decel": 1.0, "distance": 0.0}
    assert profile.travel_time(5.0) == math.inf  # a non-positive factor stalls the AGV
    assert profile.motion(5.0)["v_max"] == 0.0

    class Constant:
        def travel_time(self, distance: float, *args: object, **kwargs: object) -> float:
            return distance

        def motion(self, distance: float, *args: object) -> dict[str, object]:
            return {"curve": "constant", "speed": 1.0, "distance": distance}

    class Opaque:
        def travel_time(self, distance: float, *args: object, **kwargs: object) -> float:
            return distance

    assert describe_motion(Constant(), 3.0, 0.0, 1.0, None) == {"curve": "constant", "speed": 1.0, "distance": 3.0}
    assert describe_motion(Opaque(), 3.0, 0.0, 1.0, None) is LINEAR_MOTION


def test_move_events_carry_the_segment() -> None:
    env = Environment(debug=True)
    replay = FleetReplay(env)
    coordinator, (agv,), wh_a, wh_b, world = _system(env)
    seen: list[DomainEvent] = []
    env.bus.subscribe(seen.append, (AgvMoveStarted, AgvMoveEnded))
    observed: list[Any] = []
    env.bus.subscribe(lambda event: observed.append(agv.snapshot()["motion"]), (AgvMoveStarted,))
    coordinator.submit(coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b))
    env.run()

    started, ended = seen[0], seen[1]
    assert isinstance(started, AgvMoveStarted) and isinstance(ended, AgvMoveEnded)
    assert (started.t, started.from_node, started.to_node, started.loaded) == (0, "C1", "OUT", False)
    assert started.t_end == 4.5
    assert started.motion == {  # the run of one arc, rest to rest (spec §9.2, §11.3)
        "curve": "piecewise",
        "v0": 0.0,
        "phases": ((2.0, 1.0), (0.5, 0.0), (2.0, -1.0)),
        "distance": 5.0,
    }
    assert observed[0] == {"from": "C1", "to": "OUT", "t_start": 0.0, "t_end": 4.5, "description": started.motion}
    assert (ended.t, ended.node, ended.battery) == (4.5, "OUT", 995.0)
    assert ended.deltas.ops == (
        ("set", agv.id, "node", "OUT"),
        ("remove", "C1", "agvs", agv.id),
        ("insert", "OUT", "agvs", 0, agv.id),
        ("set", agv.id, "battery", 995.0),
        ("set", agv.id, "motion", None),
        ("set", agv.id, "heading", agv_heading(world.canonical_graph().nodes, "C1", "OUT")),
    )
    assert [e.loaded for e in seen if isinstance(e, AgvMoveStarted)] == [False, True, True, True]
    assert agv.motion is None and replay.events > 10


def test_move_started_after_the_claim_is_granted() -> None:
    """An AGV starts its segment only once it holds the claim of the next node; the blocker's tail frees C1 1 m into
    its first segment."""
    env = Environment(debug=True)
    FleetReplay(env)
    world = declare(env, _line_graph()[1])
    sized = dataclasses.replace(_agv_type(), length=1.0)
    blocker, waiter = (AGV(env=env, agv_type=sized, initial_node=world.ref(node)) for node in ("C1", "OUT"))
    traffic = ReservationController(graph=world.graph, env=env)
    coordinator = StillFleet(
        env=env,
        graph=world.graph,
        fleet=[blocker, waiter],
        warehouses=[],
        charging_stations=[],
        traffic_controller=traffic,
    )
    started: list[tuple[float, str, str, str]] = []

    def on_started(event: AgvMoveStarted) -> None:
        agv = blocker if event.agv == blocker.id else waiter
        to_node = next(node for node in nodes if node.id == event.to_node)
        assert to_node in traffic.ledger.held(agv)  # granted before the segment starts
        started.append((event.t, event.agv, event.from_node, event.to_node))

    env.bus.subscribe(on_started, (AgvMoveStarted,))
    env.activate()
    nodes = [world.node(name) for name in ("OUT", "C1", "C2", "IN")]
    out, c1, c2, end = nodes
    # The waiter asks first and waits for C1, which the blocker holds until its tail has cleared it.
    env.process(coordinator._executor.travel(waiter, c1, loaded=False))
    env.process(coordinator._executor.travel(blocker, end, loaded=False))
    env.run()

    assert started == [
        (0, blocker.id, "C1", "C2"),
        (pytest.approx(math.sqrt(2.0)), waiter.id, "OUT", "C1"),  # 1 m at acceleration 1 from rest
        (3.5, blocker.id, "C2", "IN"),  # the blocker passes C2 at speed: 2 m accelerating, then 3 m at 2 m/s
    ]
    assert (waiter.current_node, blocker.current_node) == (c1, end)


def test_a_shared_profile_describes_each_run_with_the_limits_sampled_at_its_start() -> None:
    """The two AGVs share one profile whose speed scales with the battery. The blocker (full battery) runs at up to
    2 m/s; the waiter (half battery) waits for C1 while the blocker uses the profile, then starts with its own limits,
    1 m/s at acceleration 0.5 (spec §9.2)."""
    env = Environment(debug=True)
    shared = AGVType(
        name="shared",
        speed_profile=TrapezoidalProfile(2.0, 1.0, 1.0),
        battery_capacity=1000.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        length=0.0,
    )
    world = declare(env, _line_graph()[1])
    blocker, waiter = (AGV(env=env, agv_type=shared, initial_node=world.ref(node)) for node in ("C1", "OUT"))
    waiter.battery.level = 500.0
    traffic = ReservationController(graph=world.graph, env=env)
    coordinator = StillFleet(
        env=env,
        graph=world.graph,
        fleet=[blocker, waiter],
        warehouses=[],
        charging_stations=[],
        traffic_controller=traffic,
    )
    started: list[AgvMoveStarted] = []
    env.bus.subscribe(started.append, (AgvMoveStarted,))
    env.activate()
    c1, end = world.node("C1"), world.node("IN")
    env.process(coordinator._executor.travel(waiter, c1, loaded=False))
    env.process(coordinator._executor.travel(blocker, end, loaded=False))
    env.run()

    def peak(description: Any) -> float:
        speeds, speed = [description["v0"]], description["v0"]
        for duration, accel in description["phases"]:
            speed += duration * accel
            speeds.append(speed)
        return max(speeds)

    peaks = {agv.id: [peak(event.motion) for event in started if event.agv == agv.id] for agv in (blocker, waiter)}
    assert peaks == {blocker.id: [2.0, 2.0], waiter.id: [1.0]}
    (waiting,) = [event for event in started if event.agv == waiter.id]
    assert waiting.motion["phases"][0] == (2.0, 0.5)  # accelerating at 0.5 for 2 s


@pytest.mark.parametrize("cause", ["cancelled", "breakdown", None])
def test_interrupted_move_ends_at_the_next_node(cause: str | None) -> None:
    """An interrupt during a segment takes effect at its end node (spec §8.3)."""
    env = Environment(debug=True)
    replay = FleetReplay(env)
    coordinator, (agv,), wh_a, wh_b, world = _system(env)
    order = coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b)
    interrupted: list[AgvMoveInterrupted] = []
    env.bus.subscribe(interrupted.append, (AgvMoveInterrupted,))
    coordinator.submit(order)
    env.run(until=2.0)  # half-way through C1 -> OUT
    assert agv.motion is not None and agv.motion["to"] == "OUT"

    if cause == "cancelled":
        coordinator.cancel(order)
    else:
        coordinator._active_missions[order.id].interrupt(cause)
    env.run(until=4.5 + 1e-9)

    first = interrupted[0]
    assert (first.t, first.agv, first.node, first.reason) == (4.5, agv.id, "OUT", cause or "interrupted")
    assert first.deltas.ops == ()  # agv.move_ended cleared the motion
    assert agv.current_node == world.node("OUT") and env.entities.bind_node(world.node("OUT")).agvs[0] == agv.id
    env.run()
    if cause == "cancelled":
        assert order.status is OrderStatus.CANCELLED and agv.state is AGVState.IDLE
    else:
        assert order.status is OrderStatus.COMPLETED  # re-queued and dispatched again
    assert replay.events > 5


def test_stalled_segment_ends_at_infinity() -> None:
    env = Environment(debug=True)
    FleetReplay(env)
    world = declare(env, _line_graph()[1])
    wh_a, wh_b = _warehouses(env, world)
    stalled = TrapezoidalProfile(2.0, 1.0, 1.0, load_speed_factor_fn=lambda weight: 0.0)
    agv_type = AGVType(
        name="stalled", speed_profile=stalled, battery_capacity=1000.0, weight_capacity=100.0, volume_capacity=10.0
    )
    agv = AGV(env=env, agv_type=agv_type, initial_node=world.ref("C1"))
    coordinator = FleetCoordinator(
        env=env, graph=world.graph, fleet=[agv], warehouses=[wh_a, wh_b], charging_stations=[]
    )
    started: list[AgvMoveStarted] = []
    env.bus.subscribe(started.append, (AgvMoveStarted,))
    coordinator.submit(coordinator.create_order(sku=SKU_A, quantity=1, origin=wh_a, destination=wh_b))
    env.run(until=100)

    assert started[0].t_end == math.inf
    assert agv.motion is not None
    assert (agv.motion["t_end"], agv.motion["stalled"]) == (math.inf, True)


@pytest.mark.parametrize("reserve", [False, True])
def test_a_cancelled_loaded_order_stops_at_the_first_feasible_node_and_the_energy_is_that_of_the_arcs_covered(
    reserve: bool,
) -> None:
    """Spec §17 (stop requests): six 10 m arcs at 0.5 energy per metre, the order cancelled on the second arc at
    cruising speed. The AGV rests at ``N3``, the first node it can stop at, having paid for 3 arcs (15) and no more; it
    carries the cargo back, pays for 3 arcs again, and ends idle on ``N0`` with 970 and the order ``CANCELLED``."""
    env = Environment(debug=True)
    replay = FullReplay(env)
    world = line(env, 7)
    warehouses = [
        Warehouse(
            env=env,
            name=name,
            input_bays=[world.ref(node)],
            output_bays=[world.ref(node)],
            n_slots=2,
            products=[SKU_A],
            initial_inventory={SKU_A: stock},
            pick_time=1.0,
            put_time=1.0,
        )
        for name, node, stock in (("ORIGIN", "N0", 100), ("DEST", "N6", 0))
    ]
    kind = AGVType(
        name="cart",
        speed_profile=steady(),
        battery_capacity=1000.0,
        weight_capacity=100.0,
        volume_capacity=10.0,
        load_time=1.0,
        unload_time=1.0,
        depletion_fn=lambda distance, load_weight, speed: 0.5 * distance,
        length=0.0,
    )
    agv = AGV(env=env, agv_type=kind, agv_id="A", initial_node=world.ref("N0"))
    controller: Any = ReservationController(env=env, graph=world.graph) if reserve else FreeFlowController()
    fleet = FleetCoordinator(
        env=env,
        graph=world.graph,
        fleet=[agv],
        warehouses=warehouses,
        charging_stations=[],
        traffic_controller=controller,
        default_metrics=False,
    )
    if reserve:
        TrafficInvariants(env, controller)
    order = fleet.create_order(sku=SKU_A, quantity=10, origin=warehouses[0], destination=warehouses[1])
    fleet.submit(order)

    def cancel() -> Any:
        yield env.timeout(14.0)  # the AGV picks (1 s) and loads (1 s), then 12 s into its first arcs
        fleet.cancel(order)

    env.process(cancel())
    env.run()

    started = replay.of(AgvMoveStarted)
    assert started[0].loaded and started[0].t == 2.0
    (interrupted,) = replay.of(AgvMoveInterrupted)
    assert (interrupted.node, interrupted.reason) == ("N3", "cancelled")
    outbound = [e for e in replay.of(AgvMoveEnded) if e.t <= interrupted.t]
    assert [(e.node, e.battery) for e in outbound] == [("N1", 995.0), ("N2", 990.0), ("N3", 985.0)]
    returning = [e for e in replay.of(AgvMoveEnded) if e.t > interrupted.t]
    assert [(e.node, e.battery) for e in returning] == [("N2", 980.0), ("N1", 975.0), ("N0", 970.0)]
    assert order.status is OrderStatus.CANCELLED and agv.state is AGVState.IDLE and agv.current_load is None
    assert agv.current_node is not None and agv.current_node.id == "N0"
    assert agv.battery.level == 970.0
    assert warehouses[0].get_inventory_level(SKU_A) == 100  # the cargo is back
