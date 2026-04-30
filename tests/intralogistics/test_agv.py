from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile


class TestAGVState:
    def test_all_states_exist(self) -> None:
        assert len(AGVState) == 7
        assert AGVState.IDLE
        assert AGVState.TRAVELING_EMPTY
        assert AGVState.WAITING_LOAD
        assert AGVState.TRAVELING_LOADED
        assert AGVState.WAITING_UNLOAD
        assert AGVState.CHARGING
        assert AGVState.STRANDED


class TestAGVType:
    def test_frozen(self, simple_speed_profile: TrapezoidalProfile) -> None:
        agv_type = AGVType(
            name="standard",
            speed_profile=simple_speed_profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
        )
        with pytest.raises(AttributeError):
            agv_type.name = "other"  # type: ignore[misc]

    def test_defaults(self, simple_speed_profile: TrapezoidalProfile) -> None:
        agv_type = AGVType(
            name="standard",
            speed_profile=simple_speed_profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
        )
        sku = SKU(id="any", weight=1.0, volume=0.1)
        assert agv_type.compatibility_fn(sku) is True
        assert agv_type.load_time_fn() == 0.0
        assert agv_type.unload_time_fn() == 0.0


class TestAGV:
    def _make_agv(self, env: Environment, profile: TrapezoidalProfile, node: Node | None = None) -> AGV:
        agv_type = AGVType(
            name="test",
            speed_profile=profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
        )
        return AGV(env=env, agv_type=agv_type, initial_node=node)

    def test_creation(self, env: Environment, simple_speed_profile: TrapezoidalProfile, node_a: Node) -> None:
        agv = self._make_agv(env, simple_speed_profile, node_a)
        assert agv.state == AGVState.IDLE
        assert agv.current_node == node_a
        assert agv.current_load is None
        assert agv.battery.level == 100.0

    def test_auto_id(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.agv_id is not None
        assert len(agv.agv_id) > 0

    def test_custom_id(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv_type = AGVType(
            name="test",
            speed_profile=simple_speed_profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="my-agv-1")
        assert agv.agv_id == "my-agv-1"

    def test_can_carry_compatible(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile, steel_sku: SKU
    ) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.can_carry(steel_sku, quantity=10) is True

    def test_can_carry_exceeds_weight(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile, steel_sku: SKU
    ) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.can_carry(steel_sku, quantity=100) is False

    def test_can_carry_exceeds_volume(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        bulky = SKU(id="BULKY", weight=0.1, volume=1.5)
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.can_carry(bulky, quantity=2) is False

    def test_can_carry_incompatible_sku(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv_type = AGVType(
            name="test",
            speed_profile=simple_speed_profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
            compatibility_fn=lambda sku: sku.id != "HAZMAT",
        )
        agv = AGV(env=env, agv_type=agv_type)
        hazmat = SKU(id="HAZMAT", weight=1.0, volume=0.1)
        assert agv.can_carry(hazmat, quantity=1) is False

    def test_transition_to(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.state == AGVState.IDLE

        env.run(until=5.0)
        agv.transition_to(AGVState.TRAVELING_EMPTY)
        assert agv.state == AGVState.TRAVELING_EMPTY
        assert agv.state_durations[AGVState.IDLE] == pytest.approx(5.0)

        env.run(until=8.0)
        agv.transition_to(AGVState.WAITING_LOAD)
        assert agv.state_durations[AGVState.TRAVELING_EMPTY] == pytest.approx(3.0)

    def test_utilization(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        env.run(until=5.0)
        agv.transition_to(AGVState.TRAVELING_LOADED)
        env.run(until=10.0)
        agv.transition_to(AGVState.IDLE)
        assert agv.utilization() == pytest.approx(0.5)

    def test_time_allocation(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        env.run(until=10.0)
        agv.transition_to(AGVState.TRAVELING_EMPTY)
        env.run(until=20.0)
        agv.transition_to(AGVState.IDLE)
        alloc = agv.time_allocation()
        assert alloc[AGVState.IDLE] == pytest.approx(0.5)
        assert alloc[AGVState.TRAVELING_EMPTY] == pytest.approx(0.5)

    def test_state_percentage(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        env.run(until=10.0)
        agv.transition_to(AGVState.CHARGING)
        env.run(until=20.0)
        agv.transition_to(AGVState.IDLE)
        assert agv.state_percentage(AGVState.CHARGING) == pytest.approx(0.5)

    def test_metrics_at_zero_time(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        assert agv.utilization() == 0.0
        assert agv.state_percentage(AGVState.IDLE) == 0.0
        assert all(v == 0.0 for v in agv.time_allocation().values())

    def test_metrics_flush_pending_state(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv = self._make_agv(env, simple_speed_profile)
        env.run(until=10.0)
        # No transition_to — the current IDLE state has unflushed time
        assert agv.utilization() == pytest.approx(0.0)
        assert agv.state_percentage(AGVState.IDLE) == pytest.approx(1.0)
        alloc = agv.time_allocation()
        assert alloc[AGVState.IDLE] == pytest.approx(1.0)

    def test_repr(self, env: Environment, simple_speed_profile: TrapezoidalProfile) -> None:
        agv_type = AGVType(
            name="test",
            speed_profile=simple_speed_profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
        )
        agv = AGV(env=env, agv_type=agv_type, agv_id="agv-42")
        assert repr(agv) == "AGV(id='agv-42', state=IDLE)"
