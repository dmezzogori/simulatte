from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.exceptions.physical_position import (
    PhysicalPositionBusy,
    PhysicalPositionEmpty,
)
from simulatte.unitload import CaseContainer

if TYPE_CHECKING:
    from simulatte.unitload.pallet import Pallet


class PhysicalPosition:
    """
    Represent the physical position within a WarehouseLocation.
    """

    __slots__ = ("unit_load", "n_cases", "free", "busy")

    def __init__(self, unit_load: Pallet | None = None) -> None:
        self.unit_load = unit_load
        self.n_cases = 0 if unit_load is None else unit_load.n_cases
        self.free = unit_load is None
        self.busy = not self.free

    def put(self, *, unit_load: CaseContainer) -> None:
        """
        Load a unit load into the physical position.
        """

        if self.busy:
            raise PhysicalPositionBusy(self)

        self.unit_load = unit_load
        self.n_cases = unit_load.n_cases
        self.free = False
        self.busy = True

    def get(self) -> Pallet:
        """
        Empty the physical position and return the unit load.
        Raises PhysicalPositionEmpty if the physical position is free.
        """

        if self.free:
            raise PhysicalPositionEmpty(self)

        unit_load = self.unit_load
        self.unit_load = None
        self.n_cases = 0
        self.free = True
        self.busy = False

        return unit_load
