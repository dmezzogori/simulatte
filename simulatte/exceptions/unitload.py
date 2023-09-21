from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.exceptions.base import SimulationError

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation
    from simulatte.unitload.pallet import Pallet


class IncompatibleUnitLoad(SimulationError):
    def __init__(self, unit_load: Pallet, location: WarehouseLocation):
        self.unit_load = unit_load
        self.location = location
        super().__init__(f"Unit load {unit_load} is not compatible with location {location}")
