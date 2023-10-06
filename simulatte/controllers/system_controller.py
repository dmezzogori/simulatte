from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.agv import AGV
from simulatte.location import AGVRechargeLocation, InputLocation, OutputLocation
from simulatte.logger import logger
from simulatte.observables.observable_area.base import ObservableArea
from simulatte.observables.observer.base import Observer
from simulatte.utils.singleton import Singleton
from simulatte.utils.utils import as_process

if TYPE_CHECKING:
    from simpy import Process
    from simpy.resources.resource import PriorityRequest
    from simulatte.controllers.agvs_controller import AGVController
    from simulatte.controllers.cells_controller import CellsController
    from simulatte.controllers.distance_manager import DistanceController
    from simulatte.controllers.stores_controller import BaseStoresController
    from simulatte.demand.generators.base import CustomerOrdersGenerator
    from simulatte.environment import Environment
    from simulatte.location import Location
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.picking_cell.cell import PickingCell
    from simulatte.policies.agv_selection_policy.idle_feeding_selection_policy import (
        IdleFeedingSelectionPolicy,
    )
    from simulatte.policies.picking_requests_policy import PickingRequestSelectionPolicy
    from simulatte.products import Product
    from simulatte.requests import PalletRequest
    from simulatte.stores.warehouse_store import WarehouseStore
    from simulatte.typings.typings import ProcessGenerator


class IdleFeedingAGVs(ObservableArea[AGV]):
    pass


class FeedingAGVsObserver(Observer[IdleFeedingAGVs]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not kwargs["register_main_process"]:
            self.observable_area.callbacks = [self._main_process]

    def next(self) -> AGV | None:
        """
        Return the next AGV from the observable area.
        """

        if self.observable_area:
            return self.observable_area[-1]
        return None

    def _main_process(self, *args, **kwargs):
        """
        The main process of the observer.
        Get the next AGV from the observable area and delegate to the system controller
        to start a feeding operation with the AGV.
        """

        agv = self.next()
        if agv is None:
            return
        logger.debug(f"FeedingAGVsObserver - Detected {agv} as idle.")
        self.observable_area.owner.start_feeding_operation(picking_cell=agv.picking_cell)


class SystemController(metaclass=Singleton):
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
        orders_generator: CustomerOrdersGenerator,
        picking_request_selection_policy: PickingRequestSelectionPolicy,
        idle_feeding_agvs_selection_policy: IdleFeedingSelectionPolicy,
    ):
        self.env = env

        self.agv_controller = agv_controller
        self.agv_controller.register_system(system=self)

        self.cells_controller = cells_controller
        self.cells_controller.register_system(system=self)

        self.stores_controller = stores_controller
        self.stores_controller.register_system(system=self)

        self.distance_controller = distance_controller
        self.distance_controller.register_system(system=self)

        self.products = products
        self.orders_generator = orders_generator
        self.psp: list[PalletRequest] = []

        self.picking_request_selection_policy = picking_request_selection_policy
        self.idle_feeding_agvs_selection_policy = idle_feeding_agvs_selection_policy

        self.feeding_operations: list[FeedingOperation] = []
        self._finished_pallet_requests: list[PalletRequest] = []

        self.picking_cell_store_mapping: dict[PickingCell, type[WarehouseStore]] = {}

        self.input_pallet_location = InputLocation(self)
        self.system_output_location = OutputLocation(self)
        self.agv_recharge_location = AGVRechargeLocation(self)

        self.idle_feeding_agvs = IdleFeedingAGVs(signal_at="append", owner=self)
        self.feeding_agvs_observer = FeedingAGVsObserver(
            observable_area=self.idle_feeding_agvs, register_main_process=True
        )

        self.iter_shifts()
        self.pallet_requests_release()

    def __str__(self):
        return self.__class__.__name__

    @as_process
    def iter_shifts(self):
        """
        Process the pallet requests from the orders generator.

        The pallet requests are processed in a FIFO fashion.
        At fixed intervals, a new shift is requested from the orders' generator.

        The pallet requests are then put into the PSP (Pre-Shop Pool),
        for further consideration by the `check_workload` process.
        """

        # self.idle_feeding_agvs.extend(self.agv_controller.feeding_agvs)
        #
        # self.idle_feeding_agvs.trigger_signal_event(
        #     payload=EventPayload(message="Init trigger signal of IdleFeedingAGVsArea")
        # )

        # Eternal process
        while True:
            # Iter over the shifts
            for shift in self.orders_generator():
                # Add all the pallet requests of the new shift to the PSP
                self.psp.extend(shift.pallet_requests)

                # Wait for the next shift
                yield self.env.timeout(60 * 60 * 8)  # 8 hours

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
            yield agv.move_to(location=cell.output_location)

            # Wait for the pallet request to be loaded on the agv
            yield cell.get(pallet_request=pallet_request)
            yield agv.load(unit_load=pallet_request.unit_load)

            # Move the agv to the system output location
            yield agv.move_to(location=self.system_output_location)

            # Wait for the pallet request to be unloaded from the agv
            yield agv.unload()

            # Store the pallet request as finished
            self._finished_pallet_requests.append(pallet_request)

    @as_process
    def end_feeding_operation(self, *, feeding_operation: FeedingOperation) -> ProcessGenerator:
        # free the UnloadPosition associated to the FeedingOperation
        feeding_operation.unload_position.release_current()

        if feeding_operation.unit_load.n_cases > 0:
            yield feeding_operation.return_to_store()
        else:
            yield feeding_operation.drop()

    def get_store_by_cell(self, *, cell: PickingCell | None = None) -> WarehouseStore:
        raise NotImplementedError

    def start_feeding_operation(self, *, agv: AGV) -> None:
        raise NotImplementedError

    def feed(self, *, feeding_operation: FeedingOperation, ant_request: PriorityRequest):
        raise NotImplementedError

    def pallet_requests_release(self):
        """
        Abstract method for releasing pallet requests from the PSP to the picking cells.
        """

        raise NotImplementedError
