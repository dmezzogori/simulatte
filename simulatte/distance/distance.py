from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.controllers.system_controller import SystemController
    from simulatte.location import Location


class Distance:
    def __init__(self, system: SystemController, from_: Location, to: Location) -> None:
        self.system = system
        self.from_ = from_
        self.to = to

    @property
    def as_distance(self) -> float:
        raise NotImplementedError
