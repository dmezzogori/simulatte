from __future__ import annotations

from typing import TYPE_CHECKING

from .exceptions import PhysicalPositionBusy, PhysicalPositionEmpty

if TYPE_CHECKING:
    from simulatte.unitload import Pallet


class PhysicalPosition:
    """
    Represent the physical position within a WarehouseLocation.
    """

    def __init__(self, unit_load: Pallet | None = None) -> None:
        self.unit_load = unit_load

    @property
    def n_cases(self) -> int:
        """
        Return the number of cases in the physical position.
        """
        if self.unit_load is not None:
            return self.unit_load.n_cases
        return 0

    def put(self, *, unit_load: Pallet) -> None:
        """
        Load a unit load into the physical position.
        """
        if self.busy:
            raise PhysicalPositionBusy(self)
        self.unit_load = unit_load

    def get(self) -> Pallet:
        """
        Empty the physical position and return the unit load.
        Raises PhysicalPositionEmpty if the physical position is free.
        """
        if self.free:
            raise PhysicalPositionEmpty(self)
        unit_load = self.unit_load
        self.unit_load = None
        return unit_load

    @property
    def free(self) -> bool:
        """
        Return True if no unit load is in the physical position.
        """
        return self.unit_load is None

    @property
    def busy(self) -> bool:
        """
        Return True if a unit load is in the physical position.
        """
        return not self.free
