from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation
    from simulatte.unitload.pallet import Pallet


class Operation:
    def __init__(self, *, unit_load: Pallet, location: WarehouseLocation, priority: int) -> None:
        self.env = Environment()
        self.unit_load = unit_load
        self.location = location
        self.priority = priority

    @property
    def position(self) -> int:
        return self.location.x

    @property
    def floor(self) -> int:
        return self.location.y

    def __eq__(self, other) -> bool:
        return self.unit_load == other.unit_load


class InputOperation(Operation):
    """Warehouse input operation"""

    def __init__(self, *, unit_load: Pallet, location: WarehouseLocation, priority: int) -> None:
        super().__init__(unit_load=unit_load, location=location, priority=priority)
        self.lift_process = None
        self.lifted = self.env.event()


class OutputOperation(Operation):
    """Warehouse output operation"""

    pass
