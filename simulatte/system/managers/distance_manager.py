from __future__ import annotations

from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from simulatte import System
    from simulatte.distance import Distance
    from simulatte.location import Location


class DistanceManager:
    def __init__(self, *, system: System, DistanceClass: Type[Distance]) -> None:
        self.system = system
        self.DistanceClass = DistanceClass

    def __call__(self, from_: Location, to: Location) -> Distance:
        return self.DistanceClass(self.system, from_, to)
