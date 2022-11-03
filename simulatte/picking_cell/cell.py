from __future__ import annotations

import collections
from typing import TYPE_CHECKING, Iterable

from simpy import Process

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
    from simulatte.system import System
    from simulatte.resources.store import Store
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

        self.input_location = Location(name="PickingCell Input")
        self.input_queue = input_queue
        self.output_location = Location(name="PickingCell Output")
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

        self.staging_location = Location(name="StagingAreaLocation")
        self.internal_location = Location(name="InternalAreaLocation")

        self._productivity_history: list[tuple[float, float]] = []

        self._main: Process | None = None
        if register_main_process:
            self._main = self.main()

    @property
    def name(self):
        return self.__class__.__name__

    @property
    def productivity(self) -> float:
        """
        Return the productivity of the PickingCell, expressed as number of PalletRequest per unit of time.
        """
        return len(self.output_queue.items) / self.system.env.now

    def register_feeding_operation(self, *, feeding_operation: FeedingOperation) -> None:
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
        Processo di gestione di una ProductRequest.
        """
        raise NotImplementedError

    def _ant_internal_movement_process(self, *, feeding_operation: FeedingOperation):
        """
        Manage the internal movements of an Ant which moves from the StagingArea to the InternalArea.
        """
        position = None
        while position is None:
            for unload_position in self.internal_area.unload_positions:
                if not unload_position.busy:
                    position = unload_position
                    break
            yield self.system.env.timeout(0.1)

        position_request = position.request(operation=feeding_operation)
        yield position_request
        yield feeding_operation.ant.move_to(system=self.system, location=self.internal_location)
        feeding_operation.unload_position = position
        feeding_operation.ready_for_unload()
        feeding_operation.ant.waiting_to_be_unloaded()

    @as_process
    def put(self, *, pallet_request: PalletRequest) -> ProcessGenerator:
        """
        It is used by the WMS to assign a pallet request to the picking cell.
        The pallet request is put inside the input queue.
        The pallet request is then decomposed in picking requests, which are placed in the picking requests queue.
        Eventually, the feeding area signal event is triggered.
        """

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
        Gets a pallet request once it has been completed by the picking cell.
        """
        pallet_request = yield self.output_queue.get(lambda e: e == pallet_request)
        return pallet_request

    @staticmethod
    def iter_pallet_request(*, pallet_request: PalletRequest) -> Iterable[ProductRequest]:
        for layer_request in pallet_request.sub_requests:
            for product_request in layer_request.sub_requests:
                yield product_request

    @as_process
    def main(self) -> ProcessGenerator:
        """
        Processo principale della cella.

        Rimane in attesa di una PalletRequest da gestire.
        Una volta ottenuta una PalletRequest, attende la disponibilità del BuildingPoint.
        Verifica la necessità di dover posizionare una paletta di legno per iniziare il pallet di formazione.
        Genera un processo per ogni PickingRequest da gestire all'interno della PalletRequest.
        Attende il processamento di tutte le PickingRequest.
        Posiziona la PalletRequest terminata nella coda di output della cella.
        """

        while True:
            pallet_request: PalletRequest = yield self.input_queue.get()
            pallet_request.assigned(time=self.system.env.now)
            self.current_pallet_request = pallet_request

            with self.building_point.request() as building_point_request:
                yield building_point_request

                procs: list[simpy.Process] = [
                    self._process_product_request(product_request=product_request, pallet_request=pallet_request)
                    for product_request in self.iter_pallet_request(pallet_request=pallet_request)
                ]

                yield self.system.env.all_of(procs)
                yield self.output_queue.put(pallet_request)
                pallet_request.completed(time=self.system.env.now)
                self._productivity_history.append((self.system.env.now, self.productivity))
                self.system.retrieve_from_cell(cell=self, pallet_request=pallet_request)
