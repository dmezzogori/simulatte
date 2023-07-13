from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from simpy import PriorityResource
from simulatte.agv import AGVKind, AGVMission, AGVPlotter, AGVStatus, AGVTrip
from simulatte.utils import Identifiable

if TYPE_CHECKING:
    from collections.abc import Iterable

    from simulatte import Environment, as_process
    from simulatte.location import Location
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

    def __init__(
        self,
        *,
        env: Environment,
        kind: AGVKind,
        load_timeout: float,
        unload_timeout: float,
        speed: float,
    ):
        super().__init__(env, capacity=1)
        self.env = env
        self.kind = kind
        self.load_timeout = load_timeout
        self.unload_timeout = unload_timeout
        self.speed = speed
        self._status = AGVStatus.IDLE

        self._case_container: CaseContainer | None = None
        self.current_location: Location = ant_rest_location
        self._travel_time = 0
        self._mission_history: list[float] = []
        self.trips: list[AGVTrip] = []
        self._missions: list[AGVMission] = []

        self.loading_waiting_times = []
        self.loading_waiting_time_start: float | None = None

        self._waiting_to_enter_staging_area: float | None = None
        self.feeding_area_waiting_times = []

        self._waiting_to_enter_internal_area: float | None = None
        self.staging_area_waiting_times = []

        self._waiting_to_be_unloaded: float | None = None
        self.unloading_waiting_times = []

        self._waiting_picking_to_end: float | None = None
        self.picking_waiting_times = []

        self.resource_requested_timestamp = 0

        self.plotter = AGVPlotter(agv=self)

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value: AGVStatus):
        self._status = value

    def request(self, *args, **kwargs):
        self.resource_requested_timestamp = self.env.now
        self.status = AGVStatus.WAITING_UNLOADED if self._case_container is None else AGVStatus.WAITING_LOADED
        self._mission_history.append(self.env.now)
        req = super().request(*args, **kwargs)
        self._missions.append(AGVMission(agv=self, request=req))
        return req

    def release(self, *args, **kwargs):
        self._missions[-1].end_time = self.env.now
        return self.release(args, **kwargs)

    def release_current(self):
        """Release the current request the agv is taking care of"""

        if len(self.users) == 0:
            raise ValueError("AGV cannot release non-existent request.")
        return self.release(self.users[0])

    @contextmanager
    def trip(self, *, destination: Location):
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
            - The AGVTrip end_time is set to the current env.now
            - The travel time is updated
            - The current location is updated
            - The AGVTrip is appended to the mission_logs
        """

        # Initialize the trip
        trip = AGVTrip(
            agv=self, start_location=self.current_location, end_location=destination, start_time=self.env.now
        )

        # Get the distance from the current location to the destination
        distance = self.system.distance(self.current_location, destination).as_distance

        # Calculate the duration of the trip
        duration = distance / self.speed

        yield duration

        # Update the end time of the trip
        trip.end_time = self.env.now

        # Update the travel time
        self._travel_time += duration

        # Update the current location
        self.current_location = destination

        # Update the mission history
        self.trips.append(trip)

    @as_process
    def move_to(self, *, location: Location):
        with self.trip(destination=location) as duration:
            # Update the status of the AGV based on the load
            self.status = AGVStatus.TRAVELING_UNLOADED if self.unit_load is None else AGVStatus.TRAVELING_LOADED

            # Wait for the duration of the trip
            yield self.env.timeout(duration)

            # Update the status of the AGV based on the load
            self.status = AGVStatus.WAITING_UNLOADED if self.unit_load is None else AGVStatus.WAITING_LOADED

    @property
    def idle_time(self) -> float:
        return self.env.now - self.mission_time

    @property
    def saturation(self) -> float:
        return self.mission_time / self.env.now

    @property
    def missions(self) -> Iterable[tuple[float, float]]:
        yield from zip(self._mission_history[::2], self._mission_history[1::2])

    @property
    def mission_time(self) -> float:
        return sum(end - start for start, end in self.missions)

    @property
    def waiting_time(self) -> float:
        return self.mission_time - self._travel_time

    @property
    def unit_load(self) -> CaseContainer | None:
        return self._case_container

    @unit_load.setter
    def unit_load(self, value: CaseContainer | None) -> None:
        if self._case_container is not None and value is not None:
            raise RuntimeError(f"AGV [{self.id}] cannot carry two unit loads at the same time.")
        self._case_container = value

    def idle(self) -> None:
        """Set the agv to idle status"""
        self.status = AGVStatus.IDLE

    def waiting_to_be_loaded(self) -> None:
        """Set the agv to waiting status"""
        self.status = AGVStatus.WAITING_UNLOADED
        self.loading_waiting_time_start = self.env.now

    def waiting_to_be_unloaded(self) -> None:
        """Set the agv to waiting status"""
        self.status = AGVStatus.WAITING_LOADED

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

    def picking_ends(self):
        self.picking_waiting_times.append(self.env.now - self._waiting_picking_to_end)
        self._waiting_picking_to_end = None

    @as_process
    def load(self, *, unit_load: CaseContainer) -> ProcessGenerator:
        self.unit_load = unit_load
        yield self.env.timeout(self.load_timeout)
        if self.loading_waiting_time_start is not None:
            self.loading_waiting_times.append(self.env.now - self.loading_waiting_time_start)
        self.loading_waiting_time_start = None
        self.status = AGVStatus.WAITING_LOADED

    @as_process
    def unload(self) -> ProcessGenerator:
        if self.unit_load is None:
            raise ValueError("Ant cannot unload non-existent unit load.")
        yield self.env.timeout(self.unload_timeout)
        self.unit_load = None
        self.status = AGVStatus.WAITING_LOADED

    def mission_ended(self) -> None:
        self._mission_history.append(self.env.now)
        self.status = AGVStatus.IDLE
