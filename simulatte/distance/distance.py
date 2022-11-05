from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.location import Location
    from simulatte.system import System


class Distance:
    def __init__(self, system: System, from_: Location, to: Location) -> None:
        self.system = system
        self.from_ = from_
        self.to = to

    @property
    def as_distance(self) -> float:
        raise NotImplementedError

    @property
    def as_time(self) -> float:
        raise NotImplementedError
