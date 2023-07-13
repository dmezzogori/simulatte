from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from simulatte import as_process
from simulatte.logger import Logger

if TYPE_CHECKING:
    from simpy import Process
    from simpy.resources.resource import PriorityRequest
    from simulatte.controllers import (
        AGVController,
        BaseStoresController,
        CellsController,
        DistanceController,
    )
    from simulatte.environment import Environment
    from simulatte.location import Location
    from simulatte.operations import FeedingOperation
    from simulatte.picking_cell import PickingCell
    from simulatte.products import Product
    from simulatte.requests import PalletRequest
    from simulatte.stores import WarehouseStore


class SystemController:
    def __init__(
        self,
        *,
        env: Environment,
        agv_controller: AGVController,
        cells_controller: CellsController,
        stores_controller: BaseStoresController,
        distance_controller: DistanceController,
        products: list[Product],
    ):
        self.env = env
        self.agv_controller = agv_controller
        self.cells_controller = cells_controller
        self.stores_controller = stores_controller
        self.distance_controller = distance_controller

        self.products = products

        self.feeding_operations: list[FeedingOperation] = []
        self._finished_pallet_requests: list[PalletRequest] = []

        self.picking_cell_store_mapping: dict[PickingCell, type[WarehouseStore]] = {}

        self.input_pallet_location = Location(name="SystemInputPalletLocation")
        self.system_output_location = Location(name="SystemOutputLocation")

        self.logger = Logger()

    @property
    def agv_locations(self) -> Sequence[Location]:
        for _, stores in self.stores_controller.stores:
            for store in stores:
                yield store.input_location
                yield store.output_location

        for cell in self.cells_controller.picking_cells:
            yield cell.input_location
            yield cell.output_location
            yield cell.staging_location
            yield cell.internal_location

    def assign_to_cell(self, *, pallet_request: PalletRequest, cell: PickingCell | None = None) -> Process:
        """
        Assign a pallet request to a picking cell.
        """

        if cell is None:
            picking_cell_cls = self.cells_controller.filter_picking_cell_type_for_pallet_request(
                pallet_request=pallet_request
            )
            cell = self.cells_controller.get_best_picking_cell(cls=picking_cell_cls)
        return cell.put(pallet_request=pallet_request)

    def get_type_of_store_by_cell(self, *, cell: PickingCell | None = None) -> type[WarehouseStore]:
        return self.picking_cell_store_mapping[type(cell)]

    def distance(self, from_: Location, to: Location):
        """
        Return the distance between two locations.
        """
        return self.distance_controller(from_=from_, to=to)

    @as_process
    def retrieve_from_cell(self, *, cell: PickingCell, pallet_request: PalletRequest) -> Process:
        """
        Retrieve a pallet request from a picking cell.
        """
        ant = self.agv_controller.get_best_retrieval_agv()
        with ant.request() as ant_request:
            yield ant_request
            ant.mission_started()
            yield ant.move_to(system=self, location=cell.output_location)
            yield cell.get(pallet_request=pallet_request)
            yield ant.load(unit_load=pallet_request.unit_load)
            yield ant.move_to(system=self, location=self.system_output_location)
            yield ant.unload()
            ant.mission_ended()
            self._finished_pallet_requests.append(pallet_request)

    def get_store_by_cell(self, *, cell: PickingCell | None = None) -> WarehouseStore:
        raise NotImplementedError

    def start_feeding_operation(self, *, cell: PickingCell) -> None:
        raise NotImplementedError

    def feed(self, *, feeding_operation: FeedingOperation, ant_request: PriorityRequest):
        raise NotImplementedError
