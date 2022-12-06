from __future__ import annotations

from typing import TYPE_CHECKING

import simpy

import simulatte

if TYPE_CHECKING:
    from simulatte.stores import WarehouseLocation, WarehouseStore
    from simulatte.unitload import CaseContainer


class Traslo(simpy.PriorityResource):
    """
    An instance of this class represents the S/R machine (or traslo).

    The main task of the traslo is to move the unitloads from the input
    conveyor to the storage locations and vice versa.
    """

    def __init__(
        self, *, system: WarehouseStore, x: int, y: int, speed_x: float, speed_y: float, load_time: float
    ) -> None:
        self.env = simulatte.Environment()
        super().__init__(self.env, capacity=1)

        self.system = system
        self.x = x
        self.y = y
        self.position = (x, y)
        self.speed_x = speed_x
        self.speed_y = speed_y
        self.location_height = self.system.location_height
        self.location_width = self.system.location_width
        self.load_time = load_time

        self.unit_load: CaseContainer | None = None

    @simulatte.as_process
    def move(self, *, location: WarehouseLocation) -> None:
        time_x = abs(self.x - location.x) * location.width / self.speed_x
        time_y = abs(self.y - location.y) * location.height / self.speed_y
        yield self.env.timeout(max(time_x, time_y))
        self.x = location.x
        self.y = location.y

    @simulatte.as_process
    def load(self, *, unit_load: CaseContainer) -> None:
        if self.unit_load is not None:
            raise RuntimeError("The traslo is already loaded.")

        yield self.env.timeout(self.load_time)
        self.unit_load = unit_load

    @simulatte.as_process
    def unload(self) -> None:
        if self.unit_load is None:
            raise RuntimeError("The traslo is not loaded.")

        yield self.env.timeout(self.load_time)
        self.unit_load = None
