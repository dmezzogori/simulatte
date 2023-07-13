from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte import SystemController
    from simulatte.distance import Distance
    from simulatte.location import Location


class DistanceController:
    def __init__(self, *, system: SystemController, DistanceClass: type[Distance]) -> None:
        self.system = system
        self.DistanceClass = DistanceClass

    def __call__(self, from_: Location, to: Location) -> Distance:
        return self.DistanceClass(self.system, from_, to)
