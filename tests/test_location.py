"""Tests for Location types."""

from __future__ import annotations

import pytest

from simulatte.location import (
    AGVRechargeLocation,
    InputLocation,
    InternalLocation,
    Location,
    OutputLocation,
    StagingLocation,
)


class TestLocation:
    """Tests for the base Location class."""

    def test_location_default_element_is_none(self) -> None:
        """Location created without element should have None."""
        loc = Location()

        assert loc.element is None

    def test_location_with_element(self) -> None:
        """Location should store the element reference."""

        class MockElement:
            pass

        element = MockElement()
        loc = Location(element=element)

        assert loc.element is element

    def test_location_coordinates_default_none(self) -> None:
        """Location x, y coordinates should default to None."""
        loc = Location()

        assert loc.x is None
        assert loc.y is None

    def test_location_coordinates_settable(self) -> None:
        """Location coordinates should be settable."""
        loc = Location()
        loc.x = 10
        loc.y = 20

        assert loc.x == 10
        assert loc.y == 20

    def test_location_name_without_element(self) -> None:
        """Location name without element should be class name."""
        loc = Location()

        assert loc.name == "Location"

    def test_location_name_with_element(self) -> None:
        """Location name with element should include element class name."""

        class MockCell:
            pass

        loc = Location(element=MockCell())

        assert loc.name == "MockCell Location"

    def test_location_str_returns_name(self) -> None:
        """str(location) should return the name."""
        loc = Location()

        assert str(loc) == loc.name

    def test_pattern_matching_with_location(self) -> None:
        """Location should support pattern matching on element."""

        class MockElement:
            pass

        element = MockElement()
        loc = Location(element=element)

        match loc:
            case Location(e):
                assert e is element
            case _:
                pytest.fail("Pattern matching failed")


class TestLocationSubclasses:
    """Tests for Location subclasses."""

    def test_input_location_is_location(self) -> None:
        """InputLocation should be a Location."""
        loc = InputLocation()

        assert isinstance(loc, Location)

    def test_output_location_is_location(self) -> None:
        """OutputLocation should be a Location."""
        loc = OutputLocation()

        assert isinstance(loc, Location)

    def test_staging_location_is_location(self) -> None:
        """StagingLocation should be a Location."""
        loc = StagingLocation()

        assert isinstance(loc, Location)

    def test_internal_location_is_location(self) -> None:
        """InternalLocation should be a Location."""
        loc = InternalLocation()

        assert isinstance(loc, Location)

    def test_agv_recharge_location_is_location(self) -> None:
        """AGVRechargeLocation should be a Location."""
        loc = AGVRechargeLocation()

        assert isinstance(loc, Location)

    def test_input_location_name(self) -> None:
        """InputLocation name should be InputLocation."""
        assert InputLocation().name == "InputLocation"

    def test_output_location_name(self) -> None:
        """OutputLocation name should be OutputLocation."""
        assert OutputLocation().name == "OutputLocation"

    def test_staging_location_name(self) -> None:
        """StagingLocation name should be StagingLocation."""
        assert StagingLocation().name == "StagingLocation"

    def test_internal_location_name(self) -> None:
        """InternalLocation name should be InternalLocation."""
        assert InternalLocation().name == "InternalLocation"

    def test_agv_recharge_location_name(self) -> None:
        """AGVRechargeLocation name should be AGVRechargeLocation."""
        assert AGVRechargeLocation().name == "AGVRechargeLocation"

    def test_subclass_with_element(self) -> None:
        """Subclasses should properly handle elements."""

        class MockCell:
            pass

        loc = InputLocation(element=MockCell())

        assert loc.name == "MockCell InputLocation"

    def test_subclass_coordinates(self) -> None:
        """Subclasses should inherit coordinate functionality."""
        loc = OutputLocation()
        loc.x = 5
        loc.y = 10

        assert loc.x == 5
        assert loc.y == 10
