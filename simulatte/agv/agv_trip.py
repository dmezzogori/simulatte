from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.controllers import SystemController
from simulatte.location import (
    AGVRechargeLocation,
    InputLocation,
    InternalLocation,
    Location,
    OutputLocation,
    StagingLocation,
)
from simulatte.picking_cell import PickingCell
from simulatte.stores import WarehouseStore

if TYPE_CHECKING:
    from simpy.resources.resource import PriorityRequest
    from simulatte.agv import AGV, AGVStatus


class AGVTrip:
    """
    Represent a trip for an AGV.
    A trip is a movement from one location to another.
    """

    __slots__ = (
        "agv",
        "start_location",
        "end_location",
        "start_time",
        "distance",
        "duration",
        "end_time",
    )

    def __init__(self, agv: AGV, destination: Location):
        self.agv = agv
        self.start_location = agv.current_location
        self.end_location = destination
        self.start_time = agv.env.now

        self.distance: float = agv.system.distance(self.start_location, self.end_location).as_distance
        self.duration: float = self.distance / agv.speed
        self.end_time: float = self.start_time + self.duration

    def define_agv_status(self) -> tuple[AGVStatus, AGVStatus]:
        """
        Return the initial and end status of the AGV for this trip.
        """

        from eagle_trays.depal import Depal

        match (self.start_location, self.end_location):
            # from the charging station
            case [
                AGVRechargeLocation(),
                OutputLocation(PickingCell())
                | OutputLocation(WarehouseStore())
                | OutputLocation(Depal())
                | InputLocation(SystemController()),
            ]:
                return AGVStatus.TRAVELING_UNLOADED, AGVStatus.WAITING_TO_BE_LOADED

            # from the input location of a picking cell
            #   to the staging location of the same picking cell
            case [InputLocation(PickingCell()), StagingLocation(PickingCell())]:
                return AGVStatus.TRAVELING_LOADED, AGVStatus.WAITING_TO_BE_UNLOADED

            # from the staging location of a picking cell
            #   to the internal location of the same picking cell
            case [StagingLocation(PickingCell()), InternalLocation(PickingCell())]:
                return AGVStatus.TRAVELING_LOADED, AGVStatus.WAITING_TO_BE_UNLOADED

            # from the internal location of a picking cell
            #   to the input location of the same picking cell OR
            #   to the input location of a warehouse store
            case [InternalLocation(PickingCell()), InputLocation(PickingCell()) | InputLocation(WarehouseStore())]:
                return AGVStatus.TRAVELING_LOADED, AGVStatus.WAITING_TO_BE_UNLOADED

            # from the output location of a picking cell
            #   to the output location of the system
            case [OutputLocation(PickingCell()), OutputLocation(SystemController())]:
                return AGVStatus.TRAVELING_LOADED, AGVStatus.WAITING_TO_BE_UNLOADED

            # from the input location of a warehouse
            #   to the agv recharging location
            case [InputLocation(WarehouseStore()), AGVRechargeLocation()]:
                return AGVStatus.TRAVELING_UNLOADED, AGVStatus.RECHARGING

            # from the input location of a warehouse
            #   to the output location of a warehouse OR
            #   to the output location of a depal
            case [InputLocation(WarehouseStore()), OutputLocation(WarehouseStore()) | OutputLocation(Depal())]:
                return AGVStatus.TRAVELING_UNLOADED, AGVStatus.WAITING_TO_BE_LOADED

            # from the output location of a warehouse
            #   to the input location of a picking cell
            case [OutputLocation(WarehouseStore()), InputLocation(PickingCell())]:
                return AGVStatus.TRAVELING_LOADED, AGVStatus.WAITING_TO_BE_UNLOADED

            # from the input location of a depal
            #   to the agv recharging location
            case [InputLocation(Depal()), AGVRechargeLocation()]:
                return AGVStatus.TRAVELING_UNLOADED, AGVStatus.RECHARGING

            # from the input location of a depal
            #   to the input location of the system
            case [InputLocation(Depal()), InputLocation(SystemController())]:
                return AGVStatus.TRAVELING_UNLOADED, AGVStatus.WAITING_TO_BE_LOADED

            # from the output location of a depal
            #   to the input location of a warehouse
            case [OutputLocation(Depal()), InputLocation(WarehouseStore())]:
                return AGVStatus.TRAVELING_LOADED, AGVStatus.WAITING_TO_BE_UNLOADED

            # from the input location of the system
            #   to the input location of a depal
            case [InputLocation(SystemController()), InputLocation(Depal())]:
                return AGVStatus.TRAVELING_LOADED, AGVStatus.WAITING_TO_BE_UNLOADED

            # from the output location of the system
            #   to the agv recharging location
            case [OutputLocation(SystemController()), AGVRechargeLocation()]:
                return AGVStatus.TRAVELING_UNLOADED, AGVStatus.RECHARGING

            # from the output location of the system
            #   to the output location of a picking cell
            case [OutputLocation(SystemController()), OutputLocation(PickingCell())]:
                return AGVStatus.TRAVELING_UNLOADED, AGVStatus.WAITING_TO_BE_LOADED

            case _:
                raise ValueError


class AGVMission:
    """
    Represent a mission for an AGV.
    An AGV mission start when a request is made and ends when the request is completed.
    """

    __slots__ = ("agv", "request", "end_time")

    def __init__(self, agv: AGV, request: PriorityRequest):
        self.agv = agv
        self.request = request
        self.end_time: float | None = None

    @property
    def start_time(self) -> float:
        """
        Return the time at which the mission started.
        The start time is the time at which the request was made.
        """

        return self.request.time

    @property
    def duration(self) -> float:
        """
        Return the duration of the mission.
        If the mission has not ended yet, return the duration up to now.
        """

        if self.end_time is None:
            return self.agv.env.now - self.start_time
        return self.end_time - self.start_time
