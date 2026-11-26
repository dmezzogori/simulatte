from __future__ import annotations

from typing import Any

import pytest
import simpy
from simpy.events import ProcessGenerator

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.fleet import FleetCoordinator
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.warehouse import Warehouse
from simulatte.layout import NodeRef

from tests.intralogistics.traffic_support import declare


@pytest.fixture
def env() -> Environment:
    return Environment()


@pytest.fixture
def charging_node(env: Environment) -> NodeRef:
    """The node ``CHARGE`` at (5, 5), declared as the network of `env`'s layout."""
    return declare(env, LayoutGraph([Node(id="CHARGE", x=5.0, y=5.0)], [])).ref("CHARGE")


@pytest.fixture
def agv_type() -> AGVType:
    return AGVType(
        name="test-agv",
        speed_profile=TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0),
        battery_capacity=100.0,
        weight_capacity=100.0,
        volume_capacity=1.0,
    )


def _make_agv(env: Environment, agv_type: AGVType, battery_level: float) -> AGV:
    agv = AGV(env=env, agv_type=agv_type)
    agv.battery.level = battery_level
    return agv


class TestChargingStationRecharge:
    def test_basic_recharge_timing(self, env: Environment, charging_node: NodeRef, agv_type: AGVType) -> None:
        """AGV with 50% battery recharges to 100%. Verify timing and battery level."""
        station = ChargingStation(
            env=env,
            name="CS1",
            node=charging_node,
            n_slots=1,
        )
        agv = _make_agv(env, agv_type, battery_level=50.0)

        # Default recharge: (target - current) * 1.0 = (100 - 50) * 1.0 = 50.0
        expected_time = 50.0

        def process() -> ProcessGenerator:
            yield from station.recharge(agv, target_pct=1.0)

        env.process(process())
        env.run()

        assert env.now == pytest.approx(expected_time)
        assert agv.battery.level == pytest.approx(100.0)
        assert agv.battery.level_pct == pytest.approx(1.0)

    def test_slot_blocking(self, env: Environment, charging_node: NodeRef, agv_type: AGVType) -> None:
        """1-slot station, two AGVs. Second AGV waits until first finishes."""
        station = ChargingStation(
            env=env,
            name="CS1",
            node=charging_node,
            n_slots=1,
        )
        agv1 = _make_agv(env, agv_type, battery_level=50.0)
        agv2 = _make_agv(env, agv_type, battery_level=50.0)

        recharge_time_each = 50.0  # (100 - 50) * 1.0

        finish_times: list[float] = []

        def recharge_agv(agv: AGV) -> ProcessGenerator:
            yield from station.recharge(agv, target_pct=1.0)
            finish_times.append(env.now)

        env.process(recharge_agv(agv1))
        env.process(recharge_agv(agv2))
        env.run()

        # Both should finish, first at 50.0, second at 100.0
        assert sorted(finish_times) == [
            pytest.approx(recharge_time_each),
            pytest.approx(recharge_time_each * 2),
        ]
        assert agv1.battery.level == pytest.approx(100.0)
        assert agv2.battery.level == pytest.approx(100.0)

    def test_station_recharge_time_overrides_battery(
        self, env: Environment, charging_node: NodeRef, agv_type: AGVType
    ) -> None:
        """Station with custom recharge_time produces different timing than AGV default."""

        # Custom recharge: twice as fast
        def fast_recharge(current_level: float, target_level: float) -> float:
            return (target_level - current_level) * 0.5

        station = ChargingStation(
            env=env,
            name="CS-fast",
            node=charging_node,
            n_slots=1,
            recharge_time=fast_recharge,
        )
        agv = _make_agv(env, agv_type, battery_level=50.0)

        # Station override: (100 - 50) * 0.5 = 25.0 (vs battery default 50.0)
        expected_time = 25.0

        def process() -> ProcessGenerator:
            yield from station.recharge(agv, target_pct=1.0)

        env.process(process())
        env.run()

        assert env.now == pytest.approx(expected_time)
        assert agv.battery.level == pytest.approx(100.0)


class TestChargingStationSwap:
    def test_swap_with_available_pool(self, env: Environment, charging_node: NodeRef, agv_type: AGVType) -> None:
        """Station with available pool batteries. Swap is near-instant (swap_time)."""
        station = ChargingStation(
            env=env,
            name="CS-swap",
            node=charging_node,
            n_slots=2,
            supports_swap=True,
            swap_pool_size=2,
            swap_time=1.0,
            swap_recharge_time=60.0,
        )
        agv = _make_agv(env, agv_type, battery_level=20.0)

        finish_time: float = -1.0

        def process() -> ProcessGenerator:
            nonlocal finish_time
            yield from station.swap(agv)
            finish_time = env.now

        env.process(process())
        env.run()

        # The swap itself finishes at swap_time=1.0; background replenish runs later
        assert finish_time == pytest.approx(1.0)
        assert agv.battery.level == pytest.approx(agv.battery.capacity)

    def test_swap_with_empty_pool(self, env: Environment, charging_node: NodeRef, agv_type: AGVType) -> None:
        """Pool depleted, AGV must wait for swap_recharge_time before pool replenishes."""
        station = ChargingStation(
            env=env,
            name="CS-swap",
            node=charging_node,
            n_slots=2,
            supports_swap=True,
            swap_pool_size=1,
            swap_time=1.0,
            swap_recharge_time=10.0,
        )

        agv1 = _make_agv(env, agv_type, battery_level=20.0)
        agv2 = _make_agv(env, agv_type, battery_level=30.0)

        finish_times: list[float] = []

        def swap_agv(agv: AGV) -> ProcessGenerator:
            yield from station.swap(agv)
            finish_times.append(env.now)

        env.process(swap_agv(agv1))
        env.process(swap_agv(agv2))
        env.run()

        # AGV1 swaps at t=1 (swap_time). Its depleted battery starts a 10s recharge.
        # AGV2 waits for pool to replenish (t=1+10=11), then swap takes 1s -> finishes at t=12.
        assert sorted(finish_times) == [
            pytest.approx(1.0),
            pytest.approx(12.0),
        ]
        assert agv1.battery.level == pytest.approx(100.0)
        assert agv2.battery.level == pytest.approx(100.0)
        # Slot occupied: AGV1 holds slot from t=0 to t=1 (1s),
        # AGV2 holds slot from t=0 to t=12 (12s, including pool wait).
        assert station.total_occupied_time == pytest.approx(1.0 + 12.0)

    def test_swap_with_zero_pool_blocks_until_replenished(
        self, env: Environment, charging_node: NodeRef, agv_type: AGVType
    ) -> None:
        """T10: swap_pool_size=0 means the pool starts empty. swap() blocks
        until a battery is explicitly added to the pool."""
        station = ChargingStation(
            env=env,
            name="CS-swap-empty",
            node=charging_node,
            n_slots=1,
            supports_swap=True,
            swap_pool_size=0,
            swap_time=1.0,
            swap_recharge_time=60.0,
        )
        agv = _make_agv(env, agv_type, battery_level=20.0)

        finish_time: float = -1.0

        def do_swap() -> ProcessGenerator:
            nonlocal finish_time
            yield from station.swap(agv)
            finish_time = env.now

        def add_battery_later() -> ProcessGenerator:
            yield env.timeout(5.0)
            assert station._swap_pool is not None
            yield station._swap_pool.put(1)

        env.process(do_swap())
        env.process(add_battery_later())

        # At t=3, the swap should still be blocked (no battery in pool yet)
        env.run(until=3.0)
        assert finish_time == -1.0, "Swap should block when pool is empty"

        # Let the simulation finish
        env.run()

        # Battery added at t=5, swap takes swap_time=1.0, so finish at t=6
        assert finish_time == pytest.approx(6.0)
        assert agv.battery.level == pytest.approx(agv.battery.capacity)
        assert station.total_swaps == 1

    def test_swap_unsupported_raises(self, env: Environment, charging_node: NodeRef, agv_type: AGVType) -> None:
        """Station with supports_swap=False. Calling swap() raises RuntimeError."""
        station = ChargingStation(
            env=env,
            name="CS-no-swap",
            node=charging_node,
            n_slots=1,
            supports_swap=False,
        )
        agv = _make_agv(env, agv_type, battery_level=50.0)

        with pytest.raises(RuntimeError, match="Swap not supported by this station"):
            # swap() should raise immediately (before yielding anything)
            gen = station.swap(agv)
            next(gen)


class TestChargingStationRechargeEdgeCases:
    def test_recharge_already_at_target(self, env: Environment, charging_node: NodeRef, agv_type: AGVType) -> None:
        """Recharging an AGV already at target_pct should be a no-op (duration=0)."""
        station = ChargingStation(
            env=env,
            name="CS-noop",
            node=charging_node,
            n_slots=1,
        )
        agv = _make_agv(env, agv_type, battery_level=100.0)

        def process() -> ProcessGenerator:
            yield from station.recharge(agv, target_pct=1.0)

        env.process(process())
        env.run()

        assert env.now == pytest.approx(0.0)
        assert agv.battery.level == pytest.approx(100.0)
        assert station.total_recharges == 1

    def test_recharge_without_station_recharge_time(
        self, env: Environment, charging_node: NodeRef, agv_type: AGVType
    ) -> None:
        """Station with no recharge_time falls back to battery.recharge_time()."""
        station = ChargingStation(
            env=env,
            name="CS-default",
            node=charging_node,
            n_slots=1,
            recharge_time=None,
        )
        agv = _make_agv(env, agv_type, battery_level=50.0)

        def process() -> ProcessGenerator:
            yield from station.recharge(agv, target_pct=1.0)

        env.process(process())
        env.run()

        # Default battery recharge: (target - current) * 1.0 = 50.0
        assert env.now == pytest.approx(50.0)
        assert agv.battery.level == pytest.approx(100.0)


class TestChargingStationMetrics:
    def test_metrics_tracking(self, env: Environment, charging_node: NodeRef, agv_type: AGVType) -> None:
        """total_recharges, total_swaps, total_occupied_time accumulate correctly."""
        station = ChargingStation(
            env=env,
            name="CS-metrics",
            node=charging_node,
            n_slots=2,
            supports_swap=True,
            swap_pool_size=5,
            swap_time=2.0,
            swap_recharge_time=30.0,
        )

        agv1 = _make_agv(env, agv_type, battery_level=50.0)
        agv2 = _make_agv(env, agv_type, battery_level=60.0)
        agv3 = _make_agv(env, agv_type, battery_level=10.0)

        def recharge_agv(agv: AGV) -> ProcessGenerator:
            yield from station.recharge(agv, target_pct=1.0)

        def swap_agv(agv: AGV) -> ProcessGenerator:
            yield from station.swap(agv)

        env.process(recharge_agv(agv1))  # recharge: (100-50)*1.0 = 50s
        env.process(recharge_agv(agv2))  # recharge: (100-60)*1.0 = 40s
        env.process(swap_agv(agv3))  # swap: 2s
        env.run()

        assert station.total_recharges == 2
        assert station.total_swaps == 1
        # occupied_time: 50 + 40 + 2 = 92
        assert station.total_occupied_time == pytest.approx(92.0)


@pytest.mark.parametrize("mode", ["recharge", "swap"])
@pytest.mark.parametrize("phase", ["queued", "granted", "active"])
def test_interrupted_operation_releases_slot(
    env: Environment, charging_node: NodeRef, agv_type: AGVType, mode: str, phase: str
) -> None:
    station = ChargingStation(
        env=env,
        name="CS",
        node=charging_node,
        n_slots=1,
        recharge_time=10.0,
        supports_swap=True,
        swap_pool_size=1,
        swap_time=10.0,
    )
    interrupted = _make_agv(env, agv_type, 20.0)
    follower = _make_agv(env, agv_type, 30.0)
    operation = station.recharge if mode == "recharge" else station.swap
    causes = []

    def run_interrupted() -> ProcessGenerator:
        try:
            yield from operation(interrupted)
        except simpy.Interrupt as exc:
            causes.append(exc.cause)

    if phase == "queued":
        holder = _make_agv(env, agv_type, 40.0)
        env.process(station.recharge(holder))
    process = env.process(run_interrupted())
    env.activate()
    if phase == "granted":
        env.step()  # slot granted by Initialize, before the process resumes
    else:
        env.run(until=1.0)
    process.interrupt("breakdown")
    follower_process = env.process(operation(follower))
    env.run()

    assert causes == ["breakdown"]
    assert interrupted.battery.level == 20.0
    assert follower_process.triggered
    assert follower.battery.level == 100.0
    assert station._slots.count == 0
    assert not station._slots.queue
    assert station._swap_pool is not None
    assert station._swap_pool.level == 1


@pytest.mark.parametrize("phase", ["queued", "granted"])
def test_interrupted_swap_preserves_pool(
    env: Environment, charging_node: NodeRef, agv_type: AGVType, phase: str
) -> None:
    station = ChargingStation(
        env=env,
        name="CS",
        node=charging_node,
        n_slots=1,
        supports_swap=True,
        swap_pool_size=0,
        swap_time=10.0,
    )
    agv = _make_agv(env, agv_type, 20.0)

    def swap() -> ProcessGenerator:
        try:
            yield from station.swap(agv)
        except simpy.Interrupt:
            pass

    process = env.process(swap())
    env.run(until=1.0)
    pool = station._swap_pool
    assert pool is not None
    if phase == "granted":
        pool.put(1)
        env.step()  # get granted by the put callback, before swap resumes
    process.interrupt("breakdown")
    env.run()
    if phase == "queued":
        pool.put(1)
        env.run()
    assert pool.level == 1
    assert not pool.get_queue
    assert station._slots.count == 0
    assert agv.battery.level == 20.0
    assert station.total_swaps == 0


# ── Charger choice by route cost (spec §10.4) ─────────────────────────


class Plain:
    """A profile without limits: 2 m/s."""

    def travel_time(
        self, distance: float, load_weight: float = 0.0, battery_level: float = 1.0, speed_limit: float | None = None
    ) -> float:
        return distance / 2.0


def _charger_fleet(
    env: Environment, nodes: list[Node], arcs: list[Arc], stations: list[str], profile: object, **options: Any
) -> tuple[FleetCoordinator, AGV, list[ChargingStation]]:
    """A fleet of one AGV on the first of `nodes` (battery 100, one unit of energy per metre) with a charger on each
    node named in `stations`, in that order; `options` go to the ``AGVType``. The graph is declared on the layout of
    `env`, which is prepared."""
    world = declare(env, LayoutGraph(nodes, arcs))
    kind = AGVType(
        name="t",
        speed_profile=profile,  # ty: ignore[invalid-argument-type]
        battery_capacity=100.0,
        weight_capacity=10.0,
        volume_capacity=1.0,
        **options,
    )
    agv = AGV(env=env, agv_type=kind, initial_node=world.ref(nodes[0].id))
    sku = SKU(id="A", weight=1.0, volume=0.1)
    warehouse = Warehouse(
        env=env,
        name="W",
        input_bays=[world.ref(nodes[0].id)],
        output_bays=[world.ref(nodes[0].id)],
        n_slots=1,
        products=[sku],
        pick_time=1.0,
        put_time=1.0,
    )
    chargers = [ChargingStation(env=env, name=f"CS-{name}", node=world.ref(name), n_slots=1) for name in stations]
    fleet = FleetCoordinator(
        env=env,
        graph=world.graph,
        fleet=[agv],
        warehouses=[warehouse],
        charging_stations=chargers,
        default_metrics=False,
    )
    env.layout.prepare()
    return fleet, agv, chargers


@pytest.mark.parametrize("profile", [Plain(), TrapezoidalProfile(2.0, 1.0, 1.0)], ids=["no-limits", "limits"])
def test_the_reachable_charger_estimate_sums_the_route_arc_lengths(env: Environment, profile: object) -> None:
    """``A -- B`` has an explicit length of 12 m over a 10 m chord, ``B -- C`` its 10 m chord: the estimate is 22, not
    the 20 of the chords."""
    a, b, c = Node("A", 0.0, 0.0), Node("B", 10.0, 0.0), Node("C", 20.0, 0.0)
    fleet, agv, (station,) = _charger_fleet(env, [a, b, c], [Arc(a, b, length=12.0), Arc(b, c)], ["C"], profile)
    agv.battery.level = 21.9
    assert fleet._find_reachable_charger(agv) is None
    agv.battery.level = 22.0
    assert fleet._find_reachable_charger(agv) is station


def test_the_reachable_charger_estimate_reads_the_level_now(env: Environment) -> None:
    """After 100 s idle at 0.1 the level now is 20, though 30 was last accounted: a charger 25 m away is out of
    reach."""
    a, c = Node("A", 0.0, 0.0), Node("C", 25.0, 0.0)
    fleet, agv, (station,) = _charger_fleet(env, [a, c], [Arc(a, c)], ["C"], Plain(), idle_power=0.1)
    agv.battery.level = 30.0
    assert fleet._find_reachable_charger(agv) is station
    env.run(until=100.0)
    assert agv.battery.level == 30.0 and agv.battery_level_now() == 20.0
    assert fleet._find_reachable_charger(agv) is None


def test_an_arc_longer_than_its_chord_changes_the_charger_and_the_bay(env: Environment) -> None:
    """``N`` is 10 m away by its chord but 30 m along its arc; ``F`` is 20 m away. The chord ranking of the warehouse's
    helpers picks ``N``; the coordinator ranks by the default router's cost, the arc lengths, and picks ``F``."""
    a, near, far = Node("A", 0.0, 0.0), Node("N", 10.0, 0.0), Node("F", 0.0, 20.0)
    fleet, agv, (on_near, on_far) = _charger_fleet(
        env, [a, near, far], [Arc(a, near, length=30.0), Arc(a, far)], ["N", "F"], Plain()
    )
    a, near, far = ({node.id: node for node in fleet.graph.nodes}[name] for name in ("A", "N", "F"))  # canonical
    assert fleet._find_nearest_charger(agv) is on_far
    assert fleet._find_reachable_charger(agv) is on_far
    assert fleet.best_bay(agv, [near, far]) is far
    (warehouse,) = fleet.warehouses
    assert warehouse._nearest_bay(a, [near, far], fleet.graph) is near
