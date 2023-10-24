from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING, Literal

from IPython.display import Markdown, display
from simpy import Process
from simulatte.location import (
    InputLocation,
    InternalLocation,
    OutputLocation,
    StagingLocation,
)
from simulatte.operations.feeding_operation import FeedingOperation
from simulatte.picking_cell.areas.feeding_area import FeedingArea
from simulatte.picking_cell.observable_areas.internal_area import InternalArea
from simulatte.picking_cell.observable_areas.staging_area import StagingArea
from simulatte.picking_cell.observers.internal_observer import InternalObserver
from simulatte.picking_cell.observers.staging_observer import StagingObserver
from simulatte.requests import PalletRequest, ProductRequest
from simulatte.resources.monitored_resource import MonitoredResource
from simulatte.simpy_extension.sequential_store.sequential_store import SequentialStore
from simulatte.utils import Identifiable
from simulatte.utils.utils import as_process
from tabulate import tabulate

if TYPE_CHECKING:
    from collections.abc import Iterable

    from simulatte.controllers.system_controller import SystemController
    from simulatte.requests import Request
    from simulatte.resources.store import Store
    from simulatte.typings.typings import ProcessGenerator


class PickingCell(metaclass=Identifiable):
    id: int

    def __init__(
        self,
        *,
        system: SystemController,
        input_queue: Store[PalletRequest],
        output_queue: SequentialStore[PalletRequest],
        building_point: MonitoredResource,
        feeding_area_capacity: int,
        staging_area_capacity: int,
        internal_area_capacity: int,
        workload_unit: Literal["cases", "layers"],
        register_main_process: bool = True,
    ):
        self.system = system

        self.input_location = InputLocation(self)
        self.input_queue = input_queue
        self.output_location = OutputLocation(self)
        self.output_queue = output_queue
        self.building_point = building_point

        self.feeding_operations: list[FeedingOperation] = []

        self.feeding_area = FeedingArea[FeedingOperation, PickingCell](capacity=feeding_area_capacity, owner=self)

        self.staging_area = StagingArea[FeedingOperation, PickingCell](
            capacity=staging_area_capacity, owner=self, signal_at="remove"
        )
        self.staging_observer = StagingObserver(observable_area=self.staging_area)

        self.internal_area = InternalArea[FeedingOperation, PickingCell](
            capacity=internal_area_capacity, owner=self, signal_at="remove"
        )
        self.internal_observer = InternalObserver(observable_area=self.internal_area)

        self.picking_requests_queue: deque[Request] = deque()

        self.feeding_operation_map: dict[Request, list[FeedingOperation]] = defaultdict(list)

        self.current_pallet_request: PalletRequest | None = None

        self.staging_location = StagingLocation(self)
        self.internal_location = InternalLocation(self)

        self._productivity_history: list[tuple[float, float]] = []

        self.pallet_requests_assigned: list[PalletRequest] = []
        self.pallet_requests_done: list[PalletRequest] = []

        self.remaining_workload: float = 0
        self.workload_unit = workload_unit

        self._main: Process | None = None
        if register_main_process:
            self._main = self.main()
            self.starvation_check()

    @as_process
    def starvation_check(self):
        yield self.system.env.timeout(60 * 60 * 3)
        while True:
            yield self.system.env.timeout(60 * 5)
            if not self.feeding_area and not self.staging_area and not self.internal_area:
                self.system.setup_feeding_operation(picking_cell=type(self))

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

        Updates the PickingCell.feeding_operation_map,
        which maps which FeedingOperation is associated to which ProductRequest.
        It also adds the FeedingOperation to the FeedingArea.
        """

        for picking_request in feeding_operation.picking_requests:
            self.feeding_operation_map[picking_request].append(feeding_operation)
        self.feeding_area.append(feeding_operation, exceed=True)

    @as_process
    def _retrieve_feeding_operation(self, picking_request: Request) -> FeedingOperation:
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

    def let_ant_out(self, *, feeding_operation: FeedingOperation):
        return self.system.end_feeding_operation(feeding_operation=feeding_operation)

    @as_process
    def put(self, *, pallet_request: PalletRequest) -> ProcessGenerator:
        """
        Assigns a PalletRequest to the PickingCell.
        The PalletRequest is stored within the input queue.
        Moreover, the PalletRequest is then decomposed in picking requests,
        which are placed in the picking requests queue.
        Eventually, the FeedingArea signal event is triggered.
        """

        self.remaining_workload += pallet_request.total_workload[self.workload_unit]

        self.pallet_requests_assigned.append(pallet_request)
        self.picking_requests_queue.extend(self.iter_pallet_request(pallet_request=pallet_request))
        yield self.input_queue.put(pallet_request)

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
                self.building_point.current_pallet_request = pallet_request

                for product_request in self.iter_pallet_request(pallet_request=pallet_request):
                    yield self._process_product_request(product_request=product_request, pallet_request=pallet_request)

                # Once finished, positions the completed PalletRequest in the output queue of the cell
                yield self.output_queue.put(pallet_request)

                # Housekeeping
                self.pallet_requests_done.append(pallet_request)

                self.remaining_workload -= pallet_request.total_workload[self.workload_unit]

                pallet_request.completed(time=self.system.env.now)
                self._productivity_history.append((self.system.env.now, self.productivity))

                # Ask the System to handle the retrieval of the finished PalletRequest
                self.system.retrieve_from_cell(cell=self, pallet_request=pallet_request)

    def summary(self, plot=True):
        if hasattr(__builtins__, "__IPYTHON__"):
            display(Markdown(f"## Performance Summary of {self.name}"))
        else:
            print(f"## Performance Summary of {self.name}")

        hourly_cell_productivity = self.productivity * 60 * 60
        hourly_cases_productivity = sum(pallet_request.n_cases for pallet_request in self.pallet_requests_done) / (
            self.system.env.now / 60 / 60
        )
        hourly_layers_productivity = sum(
            len(pallet_request.sub_requests) for pallet_request in self.pallet_requests_done
        ) / (self.system.env.now / 60 / 60)

        headers = ["KPI", "Valore", "U.M."]
        table = [
            ["Ore simulate", f"{self.system.env.now / 60 / 60:.2f}", "[h]"],
            ["PalletRequest in coda", f"{len(self.input_queue.items)}", "[PalletRequest]"],
            ["PalletRequest completate", f"{len(self.pallet_requests_done)}", "[PalletRequest]"],
            [
                "Produttività Cella",
                f"{hourly_cell_productivity:.2f}",
                "[PalletRequest/h]",
            ],
            [
                "Produttività Cella",
                f"{hourly_cases_productivity:.2f}",
                "[Cases/h]",
            ],
            [
                "Produttività Cella",
                f"{hourly_layers_productivity:.2f}",
                "[Layers/h]",
            ],
            [
                "Produttività Robot",
                f"{self.robot.productivity * 60 * 60:.2f}",
                "[Cases/h]",
            ],
            [
                "Tempo idle Robot",
                f"{self.robot.idle_time / 3600:.2f}",
                "[h]",
            ],
            [
                "Tempo idle Robot",
                f"{(self.robot.idle_time / self.system.env.now) * 100:.2f}",
                "[%]",
            ],
            [
                "Out of Sequence",
                f"{(len(self.staging_observer.out_of_sequence) / len(self.feeding_operations)) * 100:.2f}",
                "[%]",
            ],
        ]
        print(tabulate(table, headers=headers, tablefmt="fancy_grid"))

        if plot:
            display(Markdown("## Robot"))
            self.robot.plot()
            display(Markdown("## Aree logiche/fisiche"))
            self.feeding_area.plot()
            self.staging_area.plot()
            self.internal_area.plot()
