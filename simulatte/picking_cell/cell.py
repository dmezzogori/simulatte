from __future__ import annotations

import collections
from typing import TYPE_CHECKING, Iterable

from IPython.display import Markdown, display
from simpy import Process
from tabulate import tabulate

from simulatte.location import Location
from simulatte.logger.logger import EventPayload
from simulatte.operations import FeedingOperation
from simulatte.picking_cell.areas import FeedingArea, InternalArea, StagingArea
from simulatte.picking_cell.observers import FeedingObserver, StagingObserver
from simulatte.picking_cell.observers.internal_observer import InternalObserver
from simulatte.requests import PalletRequest, ProductRequest
from simulatte.resources import MonitoredResource
from simulatte.simpy_extension import SequentialStore
from simulatte.utils.utils import as_process

if TYPE_CHECKING:
    import simpy

    from simulatte.requests import Request
    from simulatte.resources.store import Store
    from simulatte.system import System
    from simulatte.typings import ProcessGenerator


class PickingCell:
    def __init__(
        self,
        *,
        system: System,
        input_queue: Store[PalletRequest],
        output_queue: SequentialStore[PalletRequest],
        building_point: MonitoredResource,
        feeding_area_capacity: int,
        staging_area_capacity: int,
        internal_area_capacity: int,
        register_main_process: bool = True,
    ):
        self.system = system
        self.system.cells_manager(self)

        self.input_location = Location(name=f"{self.__class__.__name__} Input")
        self.input_queue = input_queue
        self.output_location = Location(name=f"{self.__class__.__name__} Output")
        self.output_queue = output_queue
        self.building_point = building_point

        self.feeding_operations: list[FeedingOperation] = []

        self.feeding_area = FeedingArea(cell=self, capacity=feeding_area_capacity)
        self.feeding_observer = FeedingObserver(system=self.system, observable_area=self.feeding_area)

        self.staging_area = StagingArea(cell=self, capacity=staging_area_capacity)
        self.staging_observer = StagingObserver(system=self.system, observable_area=self.staging_area)

        self.internal_area = InternalArea(cell=self, capacity=internal_area_capacity)
        self.internal_observer = InternalObserver(system=self.system, observable_area=self.internal_area)

        self.picking_requests_queue: collections.deque[Request] = collections.deque()

        self.feeding_operation_map: dict[Request, FeedingOperation] = {}

        self.current_pallet_request: PalletRequest | None = None

        self.staging_location = Location(name=f"{self.__class__.__name__} StagingAreaLocation")
        self.internal_location = Location(name=f"{self.__class__.__name__} InternalAreaLocation")

        self._productivity_history: list[tuple[float, float]] = []

        self.pallet_requests_assigned: list[PalletRequest] = []
        self.pallet_requests_done: list[PalletRequest] = []

        self._main: Process | None = None
        if register_main_process:
            self._main = self.main()

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def productivity(self) -> float:
        """
        Return the productivity of the PickingCell, expressed as number of PalletRequest completed per unit of time.
        """
        return len(self.pallet_requests_done) / self.system.env.now

    def register_feeding_operation(self, *, feeding_operation: FeedingOperation) -> None:
        """
        Register a FeedingOperation into the PickingCell.

        Updates the FeedingOperationMap, which maps which FeedingOperation is associated to which ProductRequest.
        It also adds the FeedingOperation to the FeedingArea.
        """
        self.feeding_operation_map[feeding_operation.picking_request] = feeding_operation
        self.feeding_area.append(feeding_operation)

    @as_process
    def _retrieve_feeding_operation(self, picking_request: Request) -> ProcessGenerator[FeedingOperation]:
        """
        FIXME: PORCATA COLOSSALE!!!
        Tocca usare un processo con attesa per evitare che ci sia una race-condition tra il momento in cui
        si registra l'associazione tra feeding_operation e picking_request e il momento in cui si
        interroga l'associazione per recuperare la feeding_operation.
        """
        while picking_request not in self.feeding_operation_map:
            yield self.system.env.timeout(0.1)
        return self.feeding_operation_map[picking_request]

    @as_process
    def _process_product_request(
        self, *, product_request: ProductRequest, pallet_request: PalletRequest
    ) -> ProcessGenerator:
        """
        Main process which manages the handling of a ProductRequest.

        To be concretely implemented in a subclass.
        """
        raise NotImplementedError

    @as_process
    def let_ant_in(self, *, feeding_operation: FeedingOperation) -> ProcessGenerator:
        """
        Manage the internal physical movements of an Ant which moves from the StagingArea to the InternalArea.
        """

        # If necessary, wait for the assigned InternalArea PreUnloadPosition to be free
        pre_unload_position_request = None
        if feeding_operation.pre_unload_position is not None:
            pre_unload_position_request = feeding_operation.pre_unload_position.request(operation=feeding_operation)
            yield pre_unload_position_request

        # Move the Ant from the StagingArea to the InternalArea
        yield feeding_operation.ant.move_to(system=self.system, location=self.internal_location)

        # Wait for the assigned InternalArea UnloadPosition to be free
        unload_position_request = feeding_operation.unload_position.request(operation=feeding_operation)
        yield unload_position_request

        if pre_unload_position_request is not None:
            # Release the assigned InternalArea PreUnloadPosition
            feeding_operation.pre_unload_position.release(pre_unload_position_request)

        # Housekeeping
        feeding_operation.ready_for_unload()
        feeding_operation.ant.waiting_to_be_unloaded()

    @as_process
    def let_ant_out(self, *, feeding_operation: FeedingOperation) -> ProcessGenerator:
        from simulatte.ant import ant_rest_location

        feeding_operation.ant.picking_ends()

        # free the UnloadPosition associated to the FeedingOperation
        feeding_operation.unload_position.release_current()

        self.internal_area.remove(feeding_operation)

        # move the Ant to the rest location
        yield feeding_operation.ant.move_to(system=self.system, location=ant_rest_location)
        yield feeding_operation.ant.unload()
        feeding_operation.ant.idle()

        # release the Ant associated to the FeedingOperation
        feeding_operation.ant.release_current()
        feeding_operation.ant.mission_ended()

    @as_process
    def put(self, *, pallet_request: PalletRequest) -> ProcessGenerator:
        """
        Assigns a PalletRequest to the PickingCell.
        The PalletRequest is stored within the input queue.
        Moreover, the PalletRequest is then decomposed in picking requests,
        which are placed in the picking requests queue.
        Eventually, the FeedingArea signal event is triggered.
        """
        self.pallet_requests_assigned.append(pallet_request)
        yield self.input_queue.put(pallet_request)
        self.picking_requests_queue.extend(self.iter_pallet_request(pallet_request=pallet_request))

        # Triggers the feeding area signal event
        # until the feeding area is full or there are no more picking requests to be processed
        while not self.feeding_area.is_full and len(self.picking_requests_queue) > 0:
            payload = EventPayload(
                event="ACTIVATING FEEDING AREA SIGNAL", type=0, value={"type": 0}  # type=0 => qualcosa può entrare
            )
            self.feeding_area.trigger_signal_event(payload=payload)
            yield self.system.env.timeout(0)

    @as_process
    def get(self, pallet_request: PalletRequest) -> ProcessGenerator[PalletRequest]:
        """
        Retrieves a completed PalletRequest.
        """
        pallet_request = yield self.output_queue.get(lambda e: e == pallet_request)
        return pallet_request

    @staticmethod
    def iter_pallet_request(*, pallet_request: PalletRequest) -> Iterable[ProductRequest]:
        """
        Iterates over the  ProductRequest of a PalletRequest.
        """
        for layer_request in pallet_request.sub_requests:
            for product_request in layer_request.sub_requests:
                yield product_request

    @as_process
    def main(self) -> ProcessGenerator:
        """
        Main PickingCell process.

        Waits for a PalletRequest to be handled.
        Once a PalletRequest is obtained, it waits for the availability of the BuildingPoint.
        Generates a handling process for each ProductRequest to be handled within the PalletRequest.
        Waits for the processing of all ProductRequest.
        Once finished, positions the completed PalletRequest in the output queue of the cell,
        and asks the System to handle the retrieval of the finished PalletRequest.
        """
        while True:

            # Wait for a PalletRequest to be handled
            pallet_request: PalletRequest = yield self.input_queue.get()
            pallet_request.assigned(time=self.system.env.now)
            self.current_pallet_request = pallet_request

            with self.building_point.request() as building_point_request:
                # Wait for the availability of the BuildingPoint
                yield building_point_request

                # Generate a handling process for each ProductRequest to be handled within the PalletRequest
                procs: list[simpy.Process] = [
                    self._process_product_request(product_request=product_request, pallet_request=pallet_request)
                    for product_request in self.iter_pallet_request(pallet_request=pallet_request)
                ]

                # Wait for the processing of all ProductRequest
                yield self.system.env.all_of(procs)

                # Once finished, positions the completed PalletRequest in the output queue of the cell
                yield self.output_queue.put(pallet_request)

                # Housekeeping
                self.pallet_requests_done.append(pallet_request)
                pallet_request.completed(time=self.system.env.now)
                self._productivity_history.append((self.system.env.now, self.productivity))

                # Ask the System to handle the retrieval of the finished PalletRequest
                self.system.retrieve_from_cell(cell=self, pallet_request=pallet_request)

    def summary(self):
        display(Markdown("## Performance Summary"))

        headers = ["KPI", "Valore", "U.M."]
        table = [
            ["Ore simulate", f"{self.system.env.now / 60 / 60:.2f}", "h"],
            ["Produttività Cella", f"{self.productivity * 60 * 60:.2f}", "[PalletRequest/h]"],
            ["Produttività Cella", f"{self.productivity * 60 * 60 * 4 * 10:.2f}", "[Cases/h]"],
            ["Produttività Robot", f"{self.robot.productivity * 60 * 60:.2f}", "[Cases/h]"],
        ]
        print(tabulate(table, headers=headers, tablefmt="fancy_grid"))
        display(Markdown("## Robot"))
        self.robot.plot()
        display(Markdown("## Aree logiche/fisiche"))
        self.feeding_area.plot()
        self.staging_area.plot()
        self.internal_area.plot()
