from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING, Iterable, Type

import matplotlib.pyplot as plt
from simpy import PriorityResource

from simulatte.location import Location
from simulatte.picking_cell import PickingCell
from simulatte.typings import ProcessGenerator
from simulatte.unitload import CaseContainer
from simulatte.utils import Identifiable, as_process

if TYPE_CHECKING:
    from simpy import Environment

    from simulatte.system import System


class AntStatus(Enum):
    """
    The status of an ant.
    """

    IDLE = auto()
    TRAVELING_UNLOADED = auto()
    TRAVELING_LOADED = auto()
    WAITING_UNLOADED = auto()
    WAITING_LOADED = auto()


class AntRestLocation(Location):
    def __init__(self):
        super().__init__(name="AntRestLocation")


ant_rest_location = AntRestLocation()


class AntMission:
    def __init__(self, ant: Ant, start_location: Location, end_location: Location):
        self.ant = ant
        self.start_location = start_location
        self.start_time = ant.env.now
        self.end_location = end_location
        self.end_time = None

    def end(self):
        self.end_time = self.ant.env.now
        self.ant.mission_logs.append(self)


class Ant(PriorityResource, metaclass=Identifiable):
    """
    Represent a generic Ant.

    The ant time is divided into 3 phases:
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

    id: int

    def __init__(self, env: Environment, kind: str, picking_cell: Type[PickingCell] | None = None, load_timeout=0, unload_timeout=0, speed=0.5) -> None:
        super().__init__(env, capacity=1)
        self.env = env
        self.kind = kind
        self.picking_cell = picking_cell
        self._case_container: CaseContainer | None = None
        self.load_timeout = load_timeout
        self.unload_timeout = unload_timeout
        self.speed = speed
        self._status = AntStatus.IDLE
        self.current_location: Location = ant_rest_location
        self._travel_time = 0
        self._mission_history: list[float] = []
        self.mission_logs: list[AntMission] = []

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

    @property
    def status(self) -> AntStatus:
        return self._status

    @status.setter
    def status(self, value: AntStatus) -> None:
        self._status = value

    @property
    def idle_time(self) -> float:
        return self.env.now - self.mission_time

    @property
    def saturation(self) -> float:
        return self.mission_time / self.env.now

    @property
    def missions(self) -> Iterable[tuple[float, float]]:
        for start, end in zip(self._mission_history[::2], self._mission_history[1::2]):
            yield start, end

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
            raise RuntimeError(f"Ant [{self.id}] cannot carry two unit loads at the same time.")
        self._case_container = value

    def idle(self) -> None:
        """Set the ant to idle status"""
        self.status = AntStatus.IDLE

    def waiting_to_be_loaded(self) -> None:
        """Set the ant to waiting status"""
        self.status = AntStatus.WAITING_UNLOADED
        self.loading_waiting_time_start = self.env.now

    def waiting_to_be_unloaded(self) -> None:
        """Set the ant to waiting status"""
        self.status = AntStatus.WAITING_LOADED

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
        self.status = AntStatus.WAITING_LOADED

    @as_process
    def unload(self) -> ProcessGenerator:
        if self.unit_load is None:
            raise ValueError("Ant cannot unload non-existent unit load.")
        yield self.env.timeout(self.unload_timeout)
        self.unit_load = None
        self.status = AntStatus.WAITING_LOADED

    @as_process
    def move_to(self, *, system: System, location: Location):
        mission = AntMission(ant=self, start_location=self.current_location, end_location=location)

        traveling = self.status in (AntStatus.TRAVELING_LOADED, AntStatus.TRAVELING_UNLOADED)
        while traveling:
            yield self.env.timeout(1)
            traveling = self.status in (AntStatus.TRAVELING_LOADED, AntStatus.TRAVELING_UNLOADED)

        distance = system.distance(self.current_location, location).as_distance
        timeout = distance / self.speed

        if self.unit_load is not None:
            self.status = AntStatus.TRAVELING_LOADED
        else:
            self.status = AntStatus.TRAVELING_UNLOADED

        yield self.env.timeout(timeout)
        self._travel_time += timeout

        mission.end()

        self.current_location = location

        if self.unit_load is not None:
            self.status = AntStatus.WAITING_LOADED
        else:
            self.status = AntStatus.WAITING_UNLOADED

    def release_current(self):
        """Release the current request the ant is taking care of"""
        if len(self.users) == 0:
            raise ValueError("Ant cannot release non-existent request.")
        self.release(self.users[0])

    def mission_started(self) -> None:
        self._mission_history.append(self.env.now)
        self.status = AntStatus.WAITING_UNLOADED

    def mission_ended(self) -> None:
        self._mission_history.append(self.env.now)
        self.status = AntStatus.IDLE

    def plot(self) -> None:
        plt.plot([(end - start) / 60 for start, end in self.missions], "o-")
        plt.title(f"Ant [{self.id}] mission duration [min]")
        plt.show()

    def request(self, *args, **kwargs):
        self.resource_requested_timestamp = self.env.now
        return super().request(*args, **kwargs)