from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.agv import AGV
from simulatte.location import AGVRechargeLocation, InputLocation, OutputLocation
from simulatte.protocols import Job
from simulatte.environment import Environment
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
    from simulatte.products import Product
    from simulatte.protocols.request import PalletRequest
    from simulatte.protocols.warehouse_store import WarehouseStoreProtocol
    from simulatte.typings.typings import ProcessGenerator


class SystemController(EnvMixin):
    """
    Coordinates high level system actions (assigning pallet requests, feeding/retrieval).
    Previously this relied on observer/policy indirection; now it just keeps a few lists
    and leaves concrete strategies to simple callables on the injected controllers.
    """

    def __init__(
        self,
        *,
        agv_controller: AGVController,
        cells_controller: CellsController,
        stores_controller: StoresController,
        products: list[Product],
        jobs_generator: JobsGenerator,
        env: Environment,
    ):
        EnvMixin.__init__(self, env=env)

        self.agv_controller = agv_controller

        self.cells_controller = cells_controller
        self.cells_controller.register_system(system=self)

        self.stores_controller = stores_controller

        self.products = products
        self.jobs_generator = jobs_generator
        self.psp: list[Job] = []

        self.feeding_operations: list[FeedingOperation] = []
        self._finished_pallet_requests: list[PalletRequest] = []

        self.input_pallet_location = InputLocation(self)
        self.system_output_location = OutputLocation(self)
        self.agv_recharge_location = AGVRechargeLocation(self)

        # Simple list replaces ObservableArea+Observer indirection
        self.idle_feeding_agvs: list[AGV] = []

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
