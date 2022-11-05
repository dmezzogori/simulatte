from __future__ import annotations

from typing import Iterable

from simpy import Process
from simpy.resources.resource import PriorityRequest

from simulatte.distance import Distance
from simulatte.environment import Environment
from simulatte.location import Location
from simulatte.logger import Logger
from simulatte.operations import FeedingOperation
from simulatte.picking_cell import PickingCell
from simulatte.requests import PalletRequest
from simulatte.stores import WarehouseStore
from simulatte.system.managers import AntsManager, StoresManager


class System:
    feeding_operations: list[FeedingOperation]
    ants_manager: AntsManager
    stores_manager: StoresManager
    cells_manager: CellsManager
    distance_manager: DistanceManager

    stores: Iterable[WarehouseStore]
    locations: Iterable[Location]

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.logger = Logger()

    def distance(self, from_: Location, to: Location) -> Distance:
        raise NotImplementedError

    def get_store_by_cell(self, *, cell: PickingCell | None = None) -> WarehouseStore:
        raise NotImplementedError

    def assign_to_cell(self, *, cell: PickingCell, pallet_request: PalletRequest) -> Process:
        raise NotImplementedError

    def retrieve_from_cell(self, *, cell: PickingCell, pallet_request: PalletRequest) -> Process:
        raise NotImplementedError

    def start_feeding_operation(self, *, cell: PickingCell) -> None:
        raise NotImplementedError

    def feed(self, *, feeding_operation: FeedingOperation, ant_request: PriorityRequest):
        raise NotImplementedError
