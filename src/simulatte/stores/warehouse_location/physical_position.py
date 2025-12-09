from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.exceptions.physical_position import (
    PhysicalPositionBusy,
    PhysicalPositionEmpty,
)

if TYPE_CHECKING:
    pass


class PhysicalPosition:
    """
    Represent the physical position within a WarehouseLocation.
    """

    __slots__ = ("unit_load", "n_cases", "free", "busy")

    def __init__(self, unit_load: object | None = None) -> None:
        self.unit_load: object | None = unit_load
        self.n_cases: int = 0 if unit_load is None else self._n_cases(unit_load)
        self.free = unit_load is None
        self.busy = not self.free

    def put(self, *, unit_load: object) -> None:
        """
        Load a unit load into the physical position.
        """

        if self.busy:
            raise PhysicalPositionBusy(self)

        self.unit_load = unit_load
        self.n_cases = self._n_cases(unit_load)
        self.free = False
        self.busy = True

    def get(self) -> object:
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

        assert unit_load is not None
        return unit_load

    @staticmethod
    def _n_cases(unit_load: object) -> int:
        n_cases = getattr(unit_load, "n_cases", 0)
        if isinstance(n_cases, dict):
            return sum(int(v) for v in n_cases.values())
        return int(n_cases)
