from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from simulatte import as_process
from simulatte.location import AGVRechargeLocation, InputLocation, OutputLocation
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
    from simulatte.typings import ProcessGenerator


class SystemController:
    """
    Base class for a system controller.

    The system controller is responsible for managing the system.
    Coordinates the actions of the different controllers and the different entities in the system.

    The system controller is responsible for:
        - Assigning pallet requests to picking cells.
        - Assigning replenishment operations to replenishment AGVs.
        - Assigning feeding operations to feeding AGVs.
        - Assigning input operations to input AGVs.
        - Assigning output operations to output AGVs.
    """

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

        self.input_pallet_location = InputLocation(self)
        self.system_output_location = OutputLocation(self)
        self.agv_recharge_location = AGVRechargeLocation(self)

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

        yield self.input_pallet_location
        yield self.system_output_location
        yield self.agv_recharge_location

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

        The pallet request is retrieved by an agv at the output location of the picking cell.
        Then the agv moves to the system output location and unloads the pallet request.
        """

        agv = self.agv_controller.best_output_agv()
        with agv.request() as request:
            # Wait for the agv to be available
            yield request

            # Move the agv to the output location of the picking cell
            yield agv.move_to(system=self, location=cell.output_location)

            # Wait for the pallet request to be loaded on the agv
            yield cell.get(pallet_request=pallet_request)
            yield agv.load(unit_load=pallet_request.unit_load)

            # Move the agv to the system output location
            yield agv.move_to(system=self, location=self.system_output_location)

            # Wait for the pallet request to be unloaded from the agv
            yield agv.unload()

            # Store the pallet request as finished
            self._finished_pallet_requests.append(pallet_request)

    def get_store_by_cell(self, *, cell: PickingCell | None = None) -> WarehouseStore:
        raise NotImplementedError

    def start_feeding_operation(self, *, cell: PickingCell) -> None:
        raise NotImplementedError

    def feed(self, *, feeding_operation: FeedingOperation, ant_request: PriorityRequest):
        raise NotImplementedError

    @as_process
    def end_feeding_operation(self, *, feeding_operation: FeedingOperation) -> ProcessGenerator:
        # free the UnloadPosition associated to the FeedingOperation
        feeding_operation.unload_position.release_current()

        # remove the FeedingOperation from the list of active feeding operations
        feeding_operation.cell.internal_area.remove(feeding_operation)

        if feeding_operation.unit_load.n_cases > 0:
            store = feeding_operation.store
            agv = feeding_operation.agv
            feeding_operation.unit_load.feeding_operation = None
            yield agv.move_to(system=self.system, location=store.input_location)
            yield self.system.stores_controller.load(store=store, ant=agv)
        else:
            # otherwise, move the Ant to the rest location
            yield feeding_operation.agv.move_to(system=self.system, location=self.system.agv_recharge_location)
            yield feeding_operation.agv.unload()
            feeding_operation.agv.release_current()
