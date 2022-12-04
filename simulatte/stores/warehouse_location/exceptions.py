from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.unitload import Pallet

    from .physical_position import PhysicalPosition
    from .warehouse_location import WarehouseLocation


class LocationBusy(Exception):
    def __init__(self, location: WarehouseLocation):
        self.location = location
        super().__init__(location)

    def __str__(self) -> str:
        return f"{self.location} is busy"


class LocationEmpty(Exception):
    def __init__(self, location: WarehouseLocation):
        self.location = location
        super().__init__(f"Location {location} is empty")


class PhysicalPositionBusy(Exception):
    def __init__(self, physical_position: PhysicalPosition):
        self.physical_position = physical_position
        super().__init__(f"Physical position {physical_position} is busy")


class PhysicalPositionEmpty(Exception):
    def __init__(self, physical_position: PhysicalPosition):
        self.physical_position = physical_position
        super().__init__(f"Physical position {physical_position} is empty")


class IncompatibleUnitLoad(Exception):
    def __init__(self, unit_load: Pallet, location: WarehouseLocation):
        self.unit_load = unit_load
        self.location = location
        super().__init__(f"Unit load {unit_load} is not compatible with location {location}")
