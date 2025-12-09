"""Tests for AGV enum types."""

from __future__ import annotations

from enum import StrEnum

from simulatte.agv.agv_kind import AGVKind
from simulatte.agv.agv_status import AGVStatus


class TestAGVKind:
    """Tests for AGVKind enumeration."""

    def test_agv_kind_is_str_enum(self) -> None:
        """AGVKind should be a StrEnum."""
        assert issubclass(AGVKind, StrEnum)

    def test_agv_kind_values(self) -> None:
        """AGVKind should define expected values."""
        assert AGVKind.FEEDING == "FEEDING"
        assert AGVKind.REPLENISHMENT == "REPLENISHMENT"
        assert AGVKind.INPUT == "INPUT"
        assert AGVKind.OUTPUT == "OUTPUT"

    def test_agv_kind_string_comparison(self) -> None:
        """AGVKind members should be comparable to strings."""
        assert AGVKind.FEEDING == "FEEDING"
        assert "FEEDING" == AGVKind.FEEDING

    def test_agv_kind_iteration(self) -> None:
        """AGVKind should be iterable."""
        kinds = list(AGVKind)

        assert len(kinds) == 4
        assert AGVKind.FEEDING in kinds

    def test_agv_kind_member_count(self) -> None:
        """AGVKind should have exactly 4 members."""
        assert len(AGVKind) == 4


class TestAGVStatus:
    """Tests for AGVStatus enumeration."""

    def test_agv_status_is_str_enum(self) -> None:
        """AGVStatus should be a StrEnum."""
        assert issubclass(AGVStatus, StrEnum)

    def test_agv_status_idle_states(self) -> None:
        """AGVStatus should define idle and recharging states."""
        assert AGVStatus.IDLE == "IDLE"
        assert AGVStatus.RECHARGING == "RECHARGING"

    def test_agv_status_traveling_states(self) -> None:
        """AGVStatus should define traveling states."""
        assert AGVStatus.TRAVELING_UNLOADED == "TRAVELING_UNLOADED"
        assert AGVStatus.TRAVELING_LOADED == "TRAVELING_LOADED"

    def test_agv_status_waiting_states(self) -> None:
        """AGVStatus should define waiting states."""
        assert AGVStatus.WAITING_TO_BE_LOADED == "WAITING_UNLOADED"
        assert AGVStatus.WAITING_TO_BE_UNLOADED == "WAITING_LOADED"

    def test_agv_status_count(self) -> None:
        """AGVStatus should have 6 states."""
        assert len(list(AGVStatus)) == 6

    def test_agv_status_iteration(self) -> None:
        """AGVStatus should be iterable."""
        statuses = list(AGVStatus)

        assert AGVStatus.IDLE in statuses
        assert AGVStatus.TRAVELING_LOADED in statuses
