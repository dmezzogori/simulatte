from __future__ import annotations

from typing import TYPE_CHECKING, cast

from simulatte.agv.agv_status import AGVStatus
from simulatte.location import Location

if TYPE_CHECKING:
    from simulatte.agv.agv import AGV


class AGVTrip:
    """
    Represent a trip for an AGV.
    A trip is a movement from one location to another.
    """

    __slots__ = (
        "agv",
        "mission",
        "start_location",
        "end_location",
        "start_time",
        "duration",
        "end_time",
    )

    def __init__(self, agv: AGV, destination: Location):
        self.agv = agv
        self.mission = agv.current_mission
        self.start_location: Location = cast(Location, agv.current_location)
        self.end_location = destination

        self.start_time = agv.env.now
        self.duration: float = self.distance / agv.speed
        self.end_time: float = self.start_time + self.duration

    @property
    def distance(self) -> float:
        raise NotImplementedError

    def define_agv_status(self) -> tuple[AGVStatus, AGVStatus]:
        raise NotImplementedError
