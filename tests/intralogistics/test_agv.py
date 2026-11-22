from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVState, AGVType
from simulatte.intralogistics.battery import Battery
from simulatte.intralogistics.events import AgvBatteryChanged
from simulatte.intralogistics.graph import Node
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.speed import TrapezoidalProfile


class TestAGVState:
    def test_all_states_exist(self) -> None:
        assert {state.name for state in AGVState} == {
            "IDLE",
            "TRAVELING_EMPTY",
            "WAITING_LOAD",
            "TRAVELING_LOADED",
            "WAITING_UNLOAD",
            "CHARGING",
            "STRANDED",
        }


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
            agv_type.name = "other"  # ty: ignore[invalid-assignment]

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
        assert agv_type.load_time == 0.0
        assert agv_type.unload_time == 0.0


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
        agv = AGV(env=env, agv_type=agv_type, agv_id="AGV-42")
        assert repr(agv) == "AGV(id='AGV-42', state=IDLE)"


class TestIdlePower:
    """``AGVType.idle_power``: energy per unit time in every state except ``CHARGING``, accounted lazily (spec §9.5)."""

    @staticmethod
    def _type(profile: TrapezoidalProfile, **options: Any) -> AGVType:
        return AGVType(
            name="test",
            speed_profile=profile,
            battery_capacity=100.0,
            weight_capacity=500.0,
            volume_capacity=2.0,
            **options,
        )

    @staticmethod
    def _listen(env: Environment) -> list[AgvBatteryChanged]:
        seen: list[AgvBatteryChanged] = []
        env.bus.subscribe(seen.append, (AgvBatteryChanged,))
        return seen

    def test_the_default_is_no_idle_power(self, simple_speed_profile: TrapezoidalProfile) -> None:
        assert self._type(simple_speed_profile).idle_power == 0.0

    @pytest.mark.parametrize("power", [-0.5, -1e-12, math.nan, math.inf, -math.inf])
    def test_a_negative_or_non_finite_idle_power_raises(
        self, simple_speed_profile: TrapezoidalProfile, power: float
    ) -> None:
        with pytest.raises(ValueError, match="idle_power"):
            self._type(simple_speed_profile, idle_power=power)

    def test_the_level_is_brought_up_to_date_and_the_change_is_emitted(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        seen = self._listen(env)
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=0.5), agv_id="A")
        env.run(until=20.0)
        assert agv.battery.level == 100.0  # lazy: nothing is accounted until something asks
        assert not seen
        agv.accrue_idle_power()
        assert agv.battery.level == 90.0
        assert [(e.t, e.battery) for e in seen] == [(20.0, 90.0)]
        agv.accrue_idle_power()  # nothing elapsed: no change, no event
        assert agv.battery.level == 90.0 and len(seen) == 1

    def test_the_level_now_subtracts_the_idle_power_not_yet_accounted_and_changes_nothing(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        seen = self._listen(env)
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=0.5), agv_id="A")
        env.run(until=20.0)
        assert agv.battery_level_now() == 90.0
        assert agv.battery.level == 100.0 and agv._accrued_at == 0.0 and not seen  # a pure read
        agv.accrue_idle_power()
        assert agv.battery_level_now() == agv.battery.level == 90.0
        env.run(until=300.0)
        assert agv.battery_level_now() == 0.0  # floored at 0, as the accrual floors it

    def test_the_level_now_is_the_accounted_level_without_idle_power_and_while_charging(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        plain = AGV(env=env, agv_type=self._type(simple_speed_profile), agv_id="P")
        charging = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=0.5), agv_id="C")
        plain.battery.level = charging.battery.level = 63.7
        charging.transition_to(AGVState.CHARGING)
        env.run(until=20.0)
        assert plain.battery_level_now() == plain.battery.level == 63.7
        assert charging.battery_level_now() == charging.battery.level == 63.7

    def test_the_level_is_updated_whether_or_not_anything_observes_it(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=0.5))
        env.run(until=20.0)
        agv.accrue_idle_power()
        assert agv.battery.level == 90.0

    def test_the_level_never_goes_below_zero_and_an_empty_battery_emits_nothing(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        seen = self._listen(env)
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=2.0))
        env.run(until=70.0)
        agv.accrue_idle_power()
        assert agv.battery.level == 0.0
        env.run(until=90.0)
        agv.accrue_idle_power()  # already empty: the level does not change
        assert agv.battery.level == 0.0
        assert [e.battery for e in seen] == [0.0]

    def test_time_spent_charging_does_not_drain(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        seen = self._listen(env)
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=0.5))
        env.run(until=10.0)
        agv.transition_to(AGVState.CHARGING)  # the idle time before the charge is accounted first
        assert agv.battery.level == 95.0
        env.run(until=30.0)
        agv.accrue_idle_power()  # at an event inside the charge: nothing accrues
        assert agv.battery.level == 95.0
        env.run(until=40.0)
        agv.transition_to(AGVState.IDLE)  # leaving the charge: time resumes from here
        assert agv.battery.level == 95.0
        env.run(until=50.0)
        agv.accrue_idle_power()
        assert agv.battery.level == 90.0
        assert [(e.t, e.battery) for e in seen] == [(10.0, 95.0), (50.0, 90.0)]

    def test_skipping_idle_power_moves_the_accounting_to_now_without_draining(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=0.5))
        env.run(until=10.0)
        agv.skip_idle_power()
        env.run(until=20.0)
        agv.accrue_idle_power()
        assert agv.battery.level == 95.0

    def test_idle_power_accrues_in_every_state_but_charging(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=1.0))
        for state in (AGVState.TRAVELING_EMPTY, AGVState.WAITING_LOAD, AGVState.STRANDED, AGVState.IDLE):
            agv.transition_to(state)
            before = agv.battery.level
            env.run(until=env.now + 1.0)
            agv.accrue_idle_power()
            assert agv.battery.level == before - 1.0

    def test_the_fold_of_a_move_drains_for_its_duration_silently(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        seen = self._listen(env)
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=0.5))
        env.run(until=8.0)
        agv.fold_idle_power(8.0)
        assert agv.battery.level == 96.0
        assert not seen  # the caller folds the level into the delta of ``agv.move_ended``
        env.run(until=12.0)
        agv.accrue_idle_power()
        assert agv.battery.level == 94.0  # the fold moved the accounting to the end of the move

    def test_an_accrual_during_a_move_is_left_to_its_fold(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile
    ) -> None:
        seen = self._listen(env)
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile, idle_power=0.5))
        agv.motion = {"from": "x", "to": "y"}  # ty: ignore[invalid-assignment]
        env.run(until=10.0)
        agv.accrue_idle_power()
        assert agv.battery.level == 100.0 and not seen

    def test_without_idle_power_nothing_is_computed_or_emitted(
        self, env: Environment, simple_speed_profile: TrapezoidalProfile, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._listen(env)

        def forbidden(self: Battery, amount: float) -> None:
            raise AssertionError("the battery was drained")

        monkeypatch.setattr(Battery, "drain", forbidden)
        agv = AGV(env=env, agv_type=self._type(simple_speed_profile))
        env.run(until=10.0)
        agv.accrue_idle_power()
        agv.transition_to(AGVState.CHARGING)
        agv.fold_idle_power(5.0)
        agv.transition_to(AGVState.IDLE)
        agv.skip_idle_power()
        assert agv.battery.level == 100.0 and not seen

    def test_it_is_part_of_the_type(self, simple_speed_profile: TrapezoidalProfile) -> None:
        base = self._type(simple_speed_profile)
        assert replace(base, idle_power=0.25).idle_power == 0.25
