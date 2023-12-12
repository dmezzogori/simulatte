from __future__ import annotations

from typing import TYPE_CHECKING

from simpy.resources.resource import PriorityRequest

if TYPE_CHECKING:
    from simulatte.agv import AGV


class AGVMission:
    """
    Represent a mission for an AGV.
    An AGV mission start when a request is made and ends when the request is completed.
    """

    __slots__ = ("agv", "request", "start_time", "end_time", "operation")

    def __init__(self, agv: AGV, request: PriorityRequest, operation=None):
        self.agv = agv
        self.request = request
        self.operation = operation
        self.start_time: float | None = None
        self.end_time: float | None = None

    @property
    def duration(self) -> float | None:
        """
        Return the duration of the mission.
        If the mission has not ended yet, return the duration up to now.
        """

        if self.start_time is None or self.end_time is None:
            return None
        return self.end_time - self.start_time
