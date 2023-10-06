from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from simpy import PriorityResource
from simulatte.agv.agv_plotter import AGVPlotter
from simulatte.agv.agv_status import AGVStatus
from simulatte.agv.agv_trip import AGVMission, AGVTrip
from simulatte.environment import Environment
from simulatte.logger import logger
from simulatte.utils import Identifiable, as_process

if TYPE_CHECKING:
    from collections.abc import Generator

    from simulatte.agv import AGVKind
    from simulatte.location import Location
    from simulatte.picking_cell import PickingCell
    from simulatte.typings import ProcessGenerator
    from simulatte.unitload import CaseContainer


class AGV(PriorityResource, metaclass=Identifiable):
    """
    Represent a generic AGV.

    The agv time is divided into 3 phases:
    - Traveling (whether loaded or unloaded)
    - Waiting (whether loaded or unloaded)
    - Idling (unloaded, no mission assigned)

    The mission time is the sum of traveling and waiting times.

    +-----------------+-----------------+-----------------+
    |     TRAVEL      |     WAITING     |       IDLE      |
    |      TIME       |      TIME       |       TIME      |
    +-----------------+-----------------+-----------------+
    |            MISSION TIME           |
    +-----------------+-----------------+
    """

    __slots__ = (
        "id",
        "env",
        "kind",
        "load_timeout",
        "unload_timeout",
        "speed",
        "picking_cell",
        "_status",
        "_case_container",
        "current_location",
        "_travel_time",
        "_travel_distance",
        "trips",
        "_missions",
        "_loading_waiting_times",
        "_loading_waiting_time_start",
        "_waiting_to_enter_staging_area",
        "feeding_area_waiting_times",
        "_waiting_to_enter_internal_area",
        "staging_area_waiting_times",
        "_waiting_to_be_unloaded",
        "unloading_waiting_times",
        "_waiting_picking_to_end",
        "picking_waiting_times",
        "plotter",
    )

    def __init__(
        self,
        *,
        kind: AGVKind,
        load_timeout: float,
        unload_timeout: float,
        speed: float,
        picking_cell: type[PickingCell] | None = None,
    ):
        env = Environment()
        super().__init__(env, capacity=1)
        self.env = env

        # Parameters
        self.kind = kind
        self.load_timeout = load_timeout
        self.unload_timeout = unload_timeout
        self.speed = speed
        self.picking_cell = picking_cell

        # Initial state
        self._status = AGVStatus.IDLE
        self._travel_time = 0
        self._travel_distance = 0

        # Keep track of the unit load on board
        self._case_container: CaseContainer | None = None

        # Keep track of the current location
        self.current_location = None

        # History of the trips
        self.trips: list[AGVTrip] = []

        # History of the missions
        self.current_mission: AGVMission | None = None
        self._missions: list[AGVMission] = []

        # Keep track of the waiting times while waiting to be loaded
        self._loading_waiting_times = []
        self._loading_waiting_time_start: float | None = None

        # Keep track of the waiting times while waiting to be unloaded
        self.unloading_waiting_times = []
        self._waiting_to_be_unloaded: float | None = None

        # Keep track of the waiting times while waiting to enter a staging area
        self.feeding_area_waiting_times = []
        self._waiting_to_enter_staging_area: float | None = None

        # Keep track of the waiting times while waiting to enter an internal area
        self.staging_area_waiting_times = []
        self._waiting_to_enter_internal_area: float | None = None

        # Keep track of the waiting times while waiting for picking to end
        self.picking_waiting_times = []
        self._waiting_picking_to_end: float | None = None

        self.plotter = AGVPlotter(agv=self)

    def __str__(self):
        return f"AGV{self.id}"

    @property
    def n_users(self) -> int:
        """
        Return the number of users of the agv.
        """
        return len(self.users)

    @property
    def n_queue(self) -> int:
        """
        Return the number of requests in the queue of the agv.
        """
        return len(self.queue)

    @property
    def status(self):
        """
        Return the status of the agv.
        """

        return self._status

    @status.setter
    def status(self, value: AGVStatus):
        """
        Set the status of the agv.
        """

        # If the new status is WAITING_TO_BE_LOADED, record the start of the loading waiting time
        if value == AGVStatus.WAITING_TO_BE_LOADED:
            if self.unit_load is not None:
                raise ValueError("AGV cannot wait to be loaded when already loaded.")

            self._loading_waiting_time_start = self.env.now

        # If the new status is WAITING_TO_BE_UNLOADED, record the end of the loading waiting time
        if value == AGVStatus.WAITING_TO_BE_UNLOADED:
            if self.unit_load is None:
                raise ValueError("AGV cannot wait to be unloaded when already unloaded.")

            if self._loading_waiting_time_start is not None:
                self._loading_waiting_times.append(self.env.now - self._loading_waiting_time_start)

            self._loading_waiting_time_start = None

        self._status = value

    @property
    def missions(self):
        return self._missions

    @property
    def total_mission_duration(self) -> float:
        """
        Return the total duration of the missions the agv has taken care of.
        """

        return sum(mission.duration for mission in self.missions if mission.duration is not None)

    def request(self, *args, operation=None, **kwargs):
        """
        Override the request method to keep track of the mission history.
        """

        def request_callback(_):
            logger.debug(f"{self} - acquired for mission {mission} for operation {operation}")

            # Set the start time of the mission
            mission.start_time = self.env.now

            # Set the current mission of the agv
            self.current_mission = mission

        # Perform the request
        request = super().request(*args, **kwargs)
        request.callbacks.append(request_callback)

        logger.debug(f"{self} - created request {request}")

        # Init the mission
        mission = AGVMission(agv=self, request=request, operation=operation)
        self._missions.append(mission)

        return request

    def release(self, *args, **kwargs):
        """
        Override the release method to keep track of the mission history.

        The release method is called when the agv is done with the current mission.
        It sets the end_time of the current mission to the current `env.now` and releases the request.
        It also sets the status of the agv to IDLE.
        """

        logger.debug(
            f"{self} - released from mission {self.current_mission} for operation {self.current_mission.operation}"
        )

        # Set the end time of the mission
        self.current_mission.end_time = self.env.now

        # Set the current mission of the agv to None
        self.current_mission = None

        # Set the status of the agv to IDLE
        self.set_idle()

        return super().release(*args, **kwargs)

    def release_current(self):
        """
        Release the current request the agv is taking care of.

        The release_current method should be called when the agv is done with a mission.
        """

        if len(self.users) == 0:
            raise ValueError("AGV cannot release non-existent request.")

        logger.debug(f"{self} - releasing from request {self.users[0]}")
        return self.release(self.users[0])

    @contextmanager
    def trip(self, *, destination: Location) -> Generator[AGVTrip, None, None]:
        """
        Perform an AGV trip to the destination location.

        This is a context manager that encapsulates a trip from the current
        location to the destination location.

        Parameters:
            destination (Location): The destination location for the trip.

        It initializes an AGVTrip object to represent the trip. It calculates
        the duration based on the distance and speed.

        The yield returns the duration of the trip.

        After the trip:
            - The AGVTrip end_time is set to the current `env.now`
            - The travel time is updated
            - The current location is updated
            - The AGVTrip is appended to the trip history
        """

        # Initialize the trip
        trip = AGVTrip(agv=self, destination=destination)

        start_status, end_status = trip.define_agv_status()
        self.status = start_status

        yield trip

        self.status = end_status

        # Update the travel time
        self._travel_time += trip.duration

        # Update the travel distance
        self._travel_distance += trip.distance

        # Update the current location
        self.current_location = destination

        # Update the mission history
        self.trips.append(trip)

    @as_process
    def move_to(self, *, location: Location) -> ProcessGenerator:
        with self.trip(destination=location) as trip:
            # Wait for the duration of the trip
            yield self.env.timeout(trip.duration)

    @property
    def idle_time(self) -> float:
        """
        Return the total idle time of the agv.
        The idle time is the time the agv has spent without a mission.

        The idle time is calculated as the difference between the current simulation time
        and the total mission duration.
        """

        return self.env.now - self.total_mission_duration

    @property
    def saturation(self) -> float:
        """
        Return the saturation of the agv.
        The saturation is the ratio between the total mission duration and the current simulation time.
        """

        return self.total_mission_duration / self.env.now

    @property
    def waiting_time(self) -> float:
        """
        Return the total waiting time of the agv.
        The waiting time is the time the agv has spent waiting while performing a mission.

        The waiting time is calculated as the difference between the total mission duration
        and the travel time.
        """

        return self.total_mission_duration - self._travel_time

    @property
    def unit_load(self) -> CaseContainer | None:
        return self._case_container

    @unit_load.setter
    def unit_load(self, value: CaseContainer | None) -> None:
        if self._case_container is not None and value is not None:
            raise RuntimeError(f"AGV [{self.id}] cannot carry two unit loads at the same time.")
        self._case_container = value

    @as_process
    def load(self, *, unit_load: CaseContainer) -> ProcessGenerator:
        """
        AGV loading process.

        The load process is a process that loads a unit load on the agv.
        The load process waits for the load timeout before loading the unit load.
        After the load timeout, the unit load is loaded on the agv and the status is set to waiting loaded.
        """

        if self.status != AGVStatus.WAITING_TO_BE_LOADED:
            raise ValueError(f"Wrong status: cannot load unit load while in status {self.status}.")

        # Wait for the load timeout
        yield self.env.timeout(self.load_timeout)

        # Load the unit load
        self.unit_load = unit_load

    @as_process
    def unload(self) -> ProcessGenerator:
        """
        AGV unloading process.

        The unload process is a process that unloads the current unit load from the agv.
        The unload process waits for the unload timeout before unloading the unit load.
        After the unload timeout, the unit load is unloaded from the agv and the status is set to waiting unloaded.

        Raises:
         - `ValueError`: If the agv does not have a unit load to unload.
        """

        # Check if the agv has a unit load to unload
        if self.unit_load is None:
            raise ValueError("Ant cannot unload non-existent unit load.")

        # Wait for the unload timeout
        yield self.env.timeout(self.unload_timeout)

        # Remove the unit load
        self.unit_load = None

    def set_idle(self) -> None:
        """Set the agv to idle status"""
        self.status = AGVStatus.IDLE

    def set_waiting_to_be_unloaded(self) -> None:
        """
        Set the agv to waiting status while loaded (unit load on board).

        To be used in the following cases:
         - Feeding AGVs: when waiting to be unloaded at the input location of a warehouse.
         - Replenishment AGVs: when waiting to be unloaded at the input location of a warehouse.
         - Input AGVs: when waiting to be unloaded at the input location of a depal.
         - Output AGVs: when waiting to be unloaded at the output location of the system.
        """

        self.status = AGVStatus.WAITING_TO_BE_UNLOADED

    def waiting_to_enter_staging_area(self) -> None:
        self._waiting_to_enter_staging_area = self.env.now

    def enter_staging_area(self) -> None:
        self.feeding_area_waiting_times.append(self.env.now - self._waiting_to_enter_staging_area)
        self._waiting_to_enter_staging_area = None
        self._waiting_to_enter_internal_area = self.env.now

    def enter_internal_area(self):
        self.staging_area_waiting_times.append(self.env.now - self._waiting_to_enter_internal_area)
        self._waiting_to_enter_internal_area = None
        self._waiting_to_be_unloaded = self.env.now

    def picking_begins(self):
        if self._waiting_to_be_unloaded is not None:
            self.unloading_waiting_times.append(self.env.now - self._waiting_to_be_unloaded)
        self._waiting_to_be_unloaded = None
        self._waiting_picking_to_end = self.env.now
