from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.agv import AGV
from simulatte.location import AGVRechargeLocation, InputLocation, OutputLocation
from simulatte.logger import logger
from simulatte.observables.observable_area.base import ObservableArea
from simulatte.observables.observer.base import Observer
from simulatte.protocols import Job
from simulatte.utils import EnvMixin
from simulatte.utils.as_process import as_process

if TYPE_CHECKING:
    from simpy.resources.resource import PriorityRequest

    from simulatte.controllers.agvs_controller import AGVController
    from simulatte.controllers.cells_controller import CellsController
    from simulatte.controllers.stores_controller import StoresController
    from simulatte.demand.jobs_generator import JobsGenerator
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.picking_cell.cell import PickingCell
    from simulatte.policies.agv_selection_policy.idle_feeding_selection_policy import (
        IdleFeedingSelectionPolicy,
    )
    from simulatte.policies.product_requests_policy import ProductRequestSelectionPolicy
    from simulatte.products import Product
    from simulatte.protocols.request import PalletRequest
    from simulatte.protocols.warehouse_store import WarehouseStoreProtocol
    from simulatte.typings.typings import ProcessGenerator


class IdleFeedingAGVs(ObservableArea[AGV, "SystemController"]):
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
        system_controller: SystemController = self.observable_area.owner
        if agv.picking_cell is not None:
            system_controller.setup_feeding_operation(picking_cell=agv.picking_cell)


class SystemController(EnvMixin):
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
        agv_controller: AGVController,
        cells_controller: CellsController,
        stores_controller: StoresController,
        products: list[Product],
        jobs_generator: JobsGenerator,
        product_requests_selection_policy: ProductRequestSelectionPolicy,
        idle_feeding_agvs_selection_policy: IdleFeedingSelectionPolicy,
    ):
        EnvMixin.__init__(self)

        self.agv_controller = agv_controller

        self.cells_controller = cells_controller
        self.cells_controller.register_system(system=self)

        self.stores_controller = stores_controller

        self.products = products
        self.jobs_generator = jobs_generator
        self.psp: list[Job] = []

        self.product_requests_selection_policy = product_requests_selection_policy
        self.idle_feeding_agvs_selection_policy = idle_feeding_agvs_selection_policy

        self.feeding_operations: list[FeedingOperation] = []
        self._finished_pallet_requests: list[PalletRequest] = []

        self.input_pallet_location = InputLocation(self)
        self.system_output_location = OutputLocation(self)
        self.agv_recharge_location = AGVRechargeLocation(self)

        self.idle_feeding_agvs = IdleFeedingAGVs(signal_at="append", owner=self)
        self.feeding_agvs_observer = FeedingAGVsObserver(
            observable_area=self.idle_feeding_agvs, register_main_process=True
        )

        self.process_jobs()
        self.pallet_requests_release()

    def __str__(self):
        return self.__class__.__name__

    @as_process
    def process_jobs(self):
        """
        Process the jobs generated.

        The jobs are processed in a FIFO fashion.
        At fixed intervals, a new shift is requested from the orders' generator.

        The pallet requests are then put into the PSP (Pre-Shop Pool),
        for further consideration by the `check_workload` process.
        """

        # Eternal process
        while True:
            # Iter over the shifts
            for shift in self.jobs_generator:
                # Add all the pallet requests of the new shift to the PSP
                self.psp.extend(shift.jobs)

                # Wait for the next shift
                yield self.env.timeout(60 * 60 * 8)  # 8 hours

    @as_process
    def retrieve_from_cell(self, *, cell: PickingCell, pallet_request: PalletRequest) -> ProcessGenerator:
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

        return None

    def end_feeding_operation(self, *, feeding_operation: FeedingOperation):
        raise NotImplementedError

    def get_store_by_cell(self, *, cell: PickingCell | None = None) -> WarehouseStoreProtocol:
        raise NotImplementedError

    def setup_feeding_operation(self, *, picking_cell: type[PickingCell]):
        """
        Abstract method for setting up a feeding operation.

        Args:
            picking_cell (PickingCell): The picking cell to setup the feeding operation for.
        """

        raise NotImplementedError

    def handle_feeding_operation(self, *, feeding_operation: FeedingOperation, agv_request: PriorityRequest):
        """
        Abstract method for handling a feeding operation.

        Args:
            feeding_operation (FeedingOperation): The feeding operation to handle.
            agv_request (PriorityRequest): The request of the agv to handle the feeding operation.
        """

        raise NotImplementedError

    def pallet_requests_release(self):
        """
        Abstract method for releasing pallet requests from the PSP to the picking cells.
        """

        raise NotImplementedError
