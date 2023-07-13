from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simpy.resources.resource import PriorityRequest
    from simulatte.agv import AGV
    from simulatte.location import Location


@dataclass
class AGVTrip:
    agv: AGV
    start_location: Location
    end_location: Location
    start_time: float
    end_time: float | None = None

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


@dataclass
class AGVMission:
    agv: AGV
    request: PriorityRequest
    end_time: float | None = None

    @property
    def start_time(self):
        return self.request.time
