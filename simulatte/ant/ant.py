from __future__ import annotations

from enum import Enum, auto
from itertools import count
from typing import TYPE_CHECKING, Iterable

import matplotlib.pyplot as plt
from simpy import PriorityResource

from simulatte.location import Location
from simulatte.typings import ProcessGenerator
from simulatte.unitload import CaseContainer
from simulatte.utils import as_process

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


class Ant(PriorityResource):
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

    _id_iter = count()

    def __init__(self, env: Environment, load_timeout=0, unload_timeout=0) -> None:
        super().__init__(env, capacity=1)
        self.id = next(self._id_iter)
        self.env = env
        self._case_container: CaseContainer | None = None
        self.load_timeout = load_timeout
        self.unload_timeout = unload_timeout
        self._status = AntStatus.IDLE
        self.current_location: Location = ant_rest_location
        self._travel_time = 0
        self._mission_history: list[float] = []

        self.loading_waiting_times = []
        self.loading_waiting_time_start: float | None = None

        self._waiting_to_enter_staging_area: float | None = None
        self.feeding_area_waiting_times = []

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
            raise ValueError(f"Ant [{self.id}] cannot carry two unit loads at the same time.")
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

    @as_process
    def load(self, *, unit_load: CaseContainer) -> ProcessGenerator:
        self.unit_load = unit_load
        yield self.env.timeout(self.load_timeout)
        if self.loading_waiting_time_start is not None:
            self.loading_waiting_times.append(self.env.now - self.loading_waiting_time_start)
        self.loading_waiting_time_start = None

    @as_process
    def unload(self) -> ProcessGenerator:
        if self.unit_load is None:
            raise ValueError("Ant cannot unload non-existent unit load.")
        yield self.env.timeout(self.unload_timeout)
        self.unit_load = None

    @as_process
    def move_to(self, *, system: System, location: Location):
        timeout = system.distance(self.current_location, location).as_time

        if self.unit_load is not None:
            self.status = AntStatus.TRAVELING_LOADED
        else:
            self.status = AntStatus.TRAVELING_UNLOADED

        yield self.env.timeout(timeout)
        self._travel_time += timeout
        self.current_location = location

    def release_current(self):
        """Release the current request the ant is taking care of"""
        if len(self.users) == 0:
            raise ValueError("Ant cannot release non-existent request.")
        self.release(self.users[0])

    def mission_started(self) -> None:
        self._mission_history.append(self.env.now)

    def mission_ended(self) -> None:
        self._mission_history.append(self.env.now)

    def plot(self) -> None:
        plt.plot([(end - start) / 60 for start, end in self.missions], "o-")
        plt.title(f"Ant [{self.id}] mission duration [min]")
        plt.show()
