from __future__ import annotations

import pytest

from simulatte.intralogistics.battery import Battery


class TestBattery:
    def test_creation_defaults(self) -> None:
        b = Battery(capacity=100.0)
        assert b.capacity == 100.0
        assert b.level == 100.0
        assert b.level_pct == pytest.approx(1.0)
        assert b.is_low is False
        assert b.is_critical is False

    def test_creation_partial_charge(self) -> None:
        b = Battery(capacity=100.0, initial_level=50.0)
        assert b.level == 50.0
        assert b.level_pct == pytest.approx(0.5)

    def test_deplete_default_fn(self) -> None:
        b = Battery(capacity=100.0)
        b.deplete(distance=10.0, load_weight=0.0, speed=1.0)
        assert b.level < 100.0

    def test_deplete_custom_fn(self) -> None:
        def custom_depletion(distance: float, load_weight: float, speed: float) -> float:
            return distance * 2.0 + load_weight * 0.1

        b = Battery(capacity=100.0, depletion_fn=custom_depletion)
        b.deplete(distance=10.0, load_weight=50.0, speed=1.0)
        assert b.level == pytest.approx(100.0 - 25.0)

    def test_deplete_clamps_at_zero(self) -> None:
        b = Battery(capacity=10.0)
        b.deplete(distance=1000.0, load_weight=0.0, speed=1.0)
        assert b.level == 0.0

    def test_is_low_threshold(self) -> None:
        b = Battery(capacity=100.0, initial_level=20.0, low_threshold=0.2)
        assert b.is_low is True
        assert b.is_critical is False

    def test_is_critical_threshold(self) -> None:
        b = Battery(capacity=100.0, initial_level=5.0, critical_threshold=0.05)
        assert b.is_critical is True

    def test_recharge_time_default(self) -> None:
        b = Battery(capacity=100.0, initial_level=50.0)
        t = b.recharge_time(target_pct=1.0)
        assert t > 0

    def test_recharge_time_custom_fn(self) -> None:
        def custom_recharge(current: float, target: float) -> float:
            return (target - current) * 0.5

        b = Battery(capacity=100.0, initial_level=60.0, recharge_fn=custom_recharge)
        t = b.recharge_time(target_pct=1.0)
        assert t == pytest.approx(20.0)

    def test_recharge(self) -> None:
        b = Battery(capacity=100.0, initial_level=50.0)
        b.recharge(amount=30.0)
        assert b.level == pytest.approx(80.0)

    def test_recharge_clamps_at_capacity(self) -> None:
        b = Battery(capacity=100.0, initial_level=90.0)
        b.recharge(amount=50.0)
        assert b.level == pytest.approx(100.0)

    def test_recharge_time_already_at_target(self) -> None:
        b = Battery(capacity=100.0, initial_level=100.0)
        t = b.recharge_time(target_pct=1.0)
        assert t == 0.0

    def test_recharge_time_above_target(self) -> None:
        b = Battery(capacity=100.0, initial_level=80.0)
        t = b.recharge_time(target_pct=0.5)
        assert t == 0.0
