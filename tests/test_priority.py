"""Tests for Priority constants."""

from __future__ import annotations

from simulatte.utils import Priority


class TestPriority:
    """Tests for Priority constant values."""

    def test_priority_values_exist(self) -> None:
        """Priority should define URGENT, HIGH, NORMAL, LOW, GTH."""
        assert hasattr(Priority, "URGENT")
        assert hasattr(Priority, "HIGH")
        assert hasattr(Priority, "NORMAL")
        assert hasattr(Priority, "LOW")
        assert hasattr(Priority, "GTH")

    def test_priority_ordering(self) -> None:
        """URGENT should be highest priority (lowest number)."""
        assert Priority.URGENT < Priority.HIGH
        assert Priority.HIGH < Priority.NORMAL
        assert Priority.NORMAL < Priority.LOW
        assert Priority.LOW < Priority.GTH

    def test_priority_specific_values(self) -> None:
        """Priority values should match expected constants."""
        assert Priority.URGENT == 10
        assert Priority.HIGH == 30
        assert Priority.NORMAL == 50
        assert Priority.LOW == 70
        assert Priority.GTH == 100
