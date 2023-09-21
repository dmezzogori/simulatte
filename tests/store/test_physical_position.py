from __future__ import annotations

import pytest
from simulatte.exceptions.physical_position import (
    PhysicalPositionBusy,
    PhysicalPositionEmpty,
)
from simulatte.stores.warehouse_location import PhysicalPosition
from simulatte.unitload import Pallet


def test_physical_position():
    physical_position = PhysicalPosition()

    # Test that the physical position is free
    assert physical_position.free

    # Test that trying to get a unit load from a free physical position raises an exception
    with pytest.raises(PhysicalPositionEmpty):
        physical_position.get()

    # Test that loading a unit load into the physical position works
    unit_load = Pallet()
    physical_position.put(unit_load=unit_load)
    assert physical_position.busy

    # Test that trying to load a unit load into a busy physical position raises an exception
    with pytest.raises(PhysicalPositionBusy):
        physical_position.put(unit_load=unit_load)

    # Test that getting the unit load from the physical position works
    assert physical_position.get() == unit_load
