"""Tests for simulatte exception hierarchy."""

from __future__ import annotations

import pytest

from simulatte.exceptions import (
    IncompatibleUnitLoad,
    LocationBusy,
    LocationEmpty,
    OutOfStockError,
    PhysicalPositionBusy,
    PhysicalPositionEmpty,
)
from simulatte.exceptions.base import SimulationError


class TestSimulationError:
    """Tests for the base SimulationError."""

    def test_simulation_error_is_exception(self) -> None:
        """SimulationError should be an Exception."""
        assert issubclass(SimulationError, Exception)

    def test_simulation_error_can_be_raised(self) -> None:
        """SimulationError should be raisable."""
        with pytest.raises(SimulationError):
            raise SimulationError("Test error")

    def test_simulation_error_message(self) -> None:
        """SimulationError should preserve its message."""
        error = SimulationError("Test message")

        assert str(error) == "Test message"


class TestLocationExceptions:
    """Tests for location-related exceptions."""

    def test_location_busy_inherits_simulation_error(self) -> None:
        """LocationBusy should inherit from SimulationError."""
        assert issubclass(LocationBusy, SimulationError)

    def test_location_busy_stores_location(self) -> None:
        """LocationBusy should store the location reference."""

        class MockLocation:
            pass

        loc = MockLocation()
        error = LocationBusy(loc)

        assert error.location is loc

    def test_location_busy_str(self) -> None:
        """LocationBusy str should indicate the location is busy."""

        class MockLocation:
            def __str__(self) -> str:
                return "TestLocation"

        error = LocationBusy(MockLocation())

        assert "busy" in str(error).lower()

    def test_location_empty_inherits_simulation_error(self) -> None:
        """LocationEmpty should inherit from SimulationError."""
        assert issubclass(LocationEmpty, SimulationError)

    def test_location_empty_stores_location(self) -> None:
        """LocationEmpty should store the location reference."""

        class MockLocation:
            pass

        loc = MockLocation()
        error = LocationEmpty(loc)

        assert error.location is loc

    def test_location_empty_str(self) -> None:
        """LocationEmpty str should indicate the location is empty."""

        class MockLocation:
            def __str__(self) -> str:
                return "TestLocation"

        error = LocationEmpty(MockLocation())

        assert "empty" in str(error).lower()


class TestPhysicalPositionExceptions:
    """Tests for physical position exceptions."""

    def test_physical_position_busy_inherits_simulation_error(self) -> None:
        """PhysicalPositionBusy should inherit from SimulationError."""
        assert issubclass(PhysicalPositionBusy, SimulationError)

    def test_physical_position_busy_stores_position(self) -> None:
        """PhysicalPositionBusy should store the position reference."""

        class MockPosition:
            pass

        pos = MockPosition()
        error = PhysicalPositionBusy(pos)

        assert error.physical_position is pos

    def test_physical_position_busy_str(self) -> None:
        """PhysicalPositionBusy str should indicate busy."""

        class MockPosition:
            def __str__(self) -> str:
                return "Position1"

        error = PhysicalPositionBusy(MockPosition())

        assert "busy" in str(error).lower()

    def test_physical_position_empty_inherits_simulation_error(self) -> None:
        """PhysicalPositionEmpty should inherit from SimulationError."""
        assert issubclass(PhysicalPositionEmpty, SimulationError)

    def test_physical_position_empty_stores_position(self) -> None:
        """PhysicalPositionEmpty should store the position reference."""

        class MockPosition:
            pass

        pos = MockPosition()
        error = PhysicalPositionEmpty(pos)

        assert error.physical_position is pos

    def test_physical_position_empty_str(self) -> None:
        """PhysicalPositionEmpty str should indicate empty."""

        class MockPosition:
            def __str__(self) -> str:
                return "Position1"

        error = PhysicalPositionEmpty(MockPosition())

        assert "empty" in str(error).lower()


class TestStoreExceptions:
    """Tests for store-related exceptions."""

    def test_out_of_stock_error_inherits_simulation_error(self) -> None:
        """OutOfStockError should inherit from SimulationError."""
        assert issubclass(OutOfStockError, SimulationError)

    def test_out_of_stock_error_can_be_raised(self) -> None:
        """OutOfStockError should be raisable."""
        with pytest.raises(OutOfStockError):
            raise OutOfStockError("Product X is out of stock")

    def test_out_of_stock_error_message(self) -> None:
        """OutOfStockError should preserve its message."""
        error = OutOfStockError("Product X is out of stock")

        assert "Product X" in str(error)


class TestUnitLoadExceptions:
    """Tests for unit load exceptions."""

    def test_incompatible_unit_load_inherits_simulation_error(self) -> None:
        """IncompatibleUnitLoad should inherit from SimulationError."""
        assert issubclass(IncompatibleUnitLoad, SimulationError)

    def test_incompatible_unit_load_can_be_raised(self) -> None:
        """IncompatibleUnitLoad should be raisable."""
        with pytest.raises(IncompatibleUnitLoad):
            raise IncompatibleUnitLoad()
