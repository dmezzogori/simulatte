from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.controllers.system_controller import SystemController
    from simulatte.distance.distance import Distance
    from simulatte.location import Location


class DistanceController:
    def __init__(self, *, DistanceClass: type[Distance]) -> None:
        self.system = None
        self.DistanceClass = DistanceClass

    def register_system(self, system: SystemController) -> None:
        self.system = system

    def __call__(self, from_: Location, to: Location) -> Distance:
        return self.DistanceClass(self.system, from_, to)
