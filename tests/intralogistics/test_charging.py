from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.charging import ChargingStation
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.speed import TrapezoidalProfile


@pytest.fixture
def env() -> Environment:
    return Environment()


@pytest.fixture
def charging_node() -> Node:
    return Node(id="CHARGE", x=5.0, y=5.0)


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
    def test_basic_recharge_timing(self, env: Environment, charging_node: Node, agv_type: AGVType) -> None:
        """AGV with 50% battery recharges to 100%. Verify timing and battery level."""
        station = ChargingStation(
            env=env,
            name="CS1",
            node=charging_node,
            n_slots=1,
        )
        agv = _make_agv(env, agv_type, battery_level=50.0)

        # Default recharge_fn: (target - current) * 1.0 = (100 - 50) * 1.0 = 50.0
        expected_time = 50.0

        def process() -> None:
            yield from station.recharge(agv, target_pct=1.0)

        env.process(process())
        env.run()

        assert env.now == pytest.approx(expected_time)
        assert agv.battery.level == pytest.approx(100.0)
        assert agv.battery.level_pct == pytest.approx(1.0)

    def test_slot_blocking(self, env: Environment, charging_node: Node, agv_type: AGVType) -> None:
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

        def recharge_agv(agv: AGV) -> None:
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

    def test_station_recharge_fn_overrides_battery(
        self, env: Environment, charging_node: Node, agv_type: AGVType
    ) -> None:
        """Station with custom recharge_fn produces different timing than AGV default."""
        # Custom recharge: twice as fast
        def fast_recharge(current_level: float, target_level: float) -> float:
            return (target_level - current_level) * 0.5

        station = ChargingStation(
            env=env,
            name="CS-fast",
            node=charging_node,
            n_slots=1,
            recharge_fn=fast_recharge,
        )
        agv = _make_agv(env, agv_type, battery_level=50.0)

        # Station override: (100 - 50) * 0.5 = 25.0 (vs battery default 50.0)
        expected_time = 25.0

        def process() -> None:
            yield from station.recharge(agv, target_pct=1.0)

        env.process(process())
        env.run()

        assert env.now == pytest.approx(expected_time)
        assert agv.battery.level == pytest.approx(100.0)


class TestChargingStationSwap:
    def test_swap_with_available_pool(self, env: Environment, charging_node: Node, agv_type: AGVType) -> None:
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

        def process() -> None:
            nonlocal finish_time
            yield from station.swap(agv)
            finish_time = env.now

        env.process(process())
        env.run()

        # The swap itself finishes at swap_time=1.0; background replenish runs later
        assert finish_time == pytest.approx(1.0)
        assert agv.battery.level == pytest.approx(agv.battery.capacity)

    def test_swap_with_empty_pool(self, env: Environment, charging_node: Node, agv_type: AGVType) -> None:
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

        def swap_agv(agv: AGV) -> None:
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
        self, env: Environment, charging_node: Node, agv_type: AGVType
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

        def do_swap() -> None:
            nonlocal finish_time
            yield from station.swap(agv)
            finish_time = env.now

        def add_battery_later() -> None:
            yield env.timeout(5.0)
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

    def test_swap_unsupported_raises(self, env: Environment, charging_node: Node, agv_type: AGVType) -> None:
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
    def test_recharge_already_at_target(
        self, env: Environment, charging_node: Node, agv_type: AGVType
    ) -> None:
        """Recharging an AGV already at target_pct should be a no-op (duration=0)."""
        station = ChargingStation(
            env=env, name="CS-noop", node=charging_node, n_slots=1,
        )
        agv = _make_agv(env, agv_type, battery_level=100.0)

        def process() -> None:
            yield from station.recharge(agv, target_pct=1.0)

        env.process(process())
        env.run()

        assert env.now == pytest.approx(0.0)
        assert agv.battery.level == pytest.approx(100.0)
        assert station.total_recharges == 1

    def test_recharge_without_station_recharge_fn(
        self, env: Environment, charging_node: Node, agv_type: AGVType
    ) -> None:
        """Station with no recharge_fn falls back to battery.recharge_time()."""
        station = ChargingStation(
            env=env, name="CS-default", node=charging_node, n_slots=1,
            recharge_fn=None,
        )
        agv = _make_agv(env, agv_type, battery_level=50.0)

        def process() -> None:
            yield from station.recharge(agv, target_pct=1.0)

        env.process(process())
        env.run()

        # Default battery recharge_fn: (target - current) * 1.0 = 50.0
        assert env.now == pytest.approx(50.0)
        assert agv.battery.level == pytest.approx(100.0)


class TestChargingStationMetrics:
    def test_metrics_tracking(self, env: Environment, charging_node: Node, agv_type: AGVType) -> None:
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

        def recharge_agv(agv: AGV) -> None:
            yield from station.recharge(agv, target_pct=1.0)

        def swap_agv(agv: AGV) -> None:
            yield from station.swap(agv)

        env.process(recharge_agv(agv1))  # recharge: (100-50)*1.0 = 50s
        env.process(recharge_agv(agv2))  # recharge: (100-60)*1.0 = 40s
        env.process(swap_agv(agv3))  # swap: 2s
        env.run()

        assert station.total_recharges == 2
        assert station.total_swaps == 1
        # occupied_time: 50 + 40 + 2 = 92
        assert station.total_occupied_time == pytest.approx(92.0)
