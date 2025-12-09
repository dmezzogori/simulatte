from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.exceptions.base import SimulationError

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class LocationBusy(SimulationError):
    def __init__(self, location: WarehouseLocation | object):
        self.location = location
        super().__init__(location)

    def __str__(self) -> str:
        return f"{self.location} is busy"


class LocationEmpty(SimulationError):
    def __init__(self, location: WarehouseLocation | object):
        self.location = location
        super().__init__(f"Location {location} is empty")
