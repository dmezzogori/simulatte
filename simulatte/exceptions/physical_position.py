from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.exceptions.base import SimulationError

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.physical_position import PhysicalPosition


class PhysicalPositionBusy(SimulationError):
    def __init__(self, physical_position: PhysicalPosition):
        self.physical_position = physical_position
        super().__init__(f"Physical position {physical_position} is busy")


class PhysicalPositionEmpty(SimulationError):
    def __init__(self, physical_position: PhysicalPosition):
        self.physical_position = physical_position
        super().__init__(f"Physical position {physical_position} is empty")
