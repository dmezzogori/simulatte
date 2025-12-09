from __future__ import annotations

from collections import deque
from itertools import groupby
from typing import TYPE_CHECKING, Literal, cast

from simpy import Process
from tabulate import tabulate

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
from simulatte.protocols.job import Job
from simulatte.requests import PalletRequest, ProductRequest
from simulatte.resources.monitored_resource import MonitoredResource
from simulatte.simpy_extension.sequential_store.sequential_store import SequentialStore
from simulatte.utils import IdentifiableMixin, as_process

if TYPE_CHECKING:
    from simulatte.controllers.system_controller import SystemController
    from simulatte.resources.store import Store
    from simulatte.robot import Robot
    from simulatte.typings.typings import ProcessGenerator


class PickingCell(IdentifiableMixin):
    def __init__(
        self,
        *,
        system: SystemController,
        input_queue: Store,
        output_queue: SequentialStore,
        building_point: MonitoredResource,
        robot: Robot,
        feeding_area_capacity: int,
        staging_area_capacity: int,
        internal_area_capacity: int,
        workload_unit: Literal["cases", "layers"],
        register_main_process: bool = True,
        env=None,
    ):
        super().__init__()

        self.system = system
        # keep picking cell env aligned with system env
        if env is None:
            env = system.env
        self.env = env

        # Locations
        self.input_location = InputLocation(self)
        self.staging_location = StagingLocation(self)
        self.internal_location = InternalLocation(self)
        self.output_location = OutputLocation(self)

        # Input and Output queues
        self.input_queue = input_queue
        self.output_queue = output_queue

        # Resource representing the availability of space needed to build a pallet
        self.building_point = building_point

        # Robot assigned to the PickingCell
        self.robot = robot

        # List of FeedingOperation created to feed the PickingCell
        self.feeding_operations: list[FeedingOperation] = []

        # List of FeedingOperation which are still to enter the PickingCell
        self.feeding_area = FeedingArea(capacity=feeding_area_capacity, owner=self)

        # List of FeedingOperation which are currently in the staging area of the PickingCell
        self.staging_area = StagingArea(capacity=staging_area_capacity, owner=self, signal_at="remove")
        # Observer of the staging area
        self.staging_observer = StagingObserver(observable_area=self.staging_area)

        # List of FeedingOperation which are currently in the internal area of the PickingCell
        self.internal_area = InternalArea(capacity=internal_area_capacity, owner=self, signal_at="remove")
        # Observer of the internal area
        self.internal_observer = InternalObserver(observable_area=self.internal_area)

        # List of PalletRequest to be handled by the PickingCell
        self.pallet_requests_assigned: list[PalletRequest] = []

        # List of PalletRequest completed by the PickingCell
        self.pallet_requests_done: list[PalletRequest] = []

        # Current PalletRequest being processed by the PickingCell
        self.current_pallet_request: PalletRequest | None = None

        # Queues of ProductRequests to be requested by the PickingCell to satisfy the PalletRequests
        self.product_requests_queue: deque[ProductRequest] = deque()

        self._productivity_history: list[tuple[float, float]] = []

        self.workload: float = 0
        self.workload_unit = workload_unit

        self._main: Process | None = None
        if register_main_process:
            self._main = self.main()
            self.starvation_check()

    @as_process
    def starvation_check(self):
        raise NotImplementedError

    @property
    def productivity(self) -> float:
        """
        Return the productivity of the PickingCell, expressed as number of PalletRequest completed per unit of time.
        """
        return len(self.pallet_requests_done) / self.system.env.now

    def register_feeding_operation(self, *, feeding_operation: FeedingOperation) -> None:
        """
        Register a FeedingOperation created to feed a PickingCell.

        Adds the FeedingOperation to the FeedingArea.

        Args:
            feeding_operation (FeedingOperation): FeedingOperation to be registered.
        """

        self.feeding_area.append_exceed(feeding_operation)

    def register_pallet_request(self, *, pallet_request: PalletRequest) -> None:
        """
        Register a PalletRequest to be handled by the PickingCell.

        Adds the PalletRequest to the assigned PalletRequests.
        Chains the ProductRequests of the PalletRequest to the PickingRequests queue.
        """

        # Add the PalletRequest to the assigned PalletRequests
        self.pallet_requests_assigned.append(pallet_request)

        # Chain the ProductRequests of the PalletRequest to the PickingRequests queue
        last_product_request = None
        if len(self.pallet_requests_assigned) > 1:
            last_product_request = self.pallet_requests_assigned[-2].sub_jobs[-1].sub_jobs[-1]

        for layer_request in pallet_request:
            for product_request in layer_request:
                product_request.prev = last_product_request
                if last_product_request is not None:
                    last_product_request.next = product_request
                last_product_request = product_request
                self.product_requests_queue.append(product_request)

    def add_workload(self, *, job: PalletRequest) -> None:
        """
        Add the workload of the PalletRequest to the total workload of the PickingCell
        """
        raise NotImplementedError

    def remove_workload(self, *, job: PalletRequest) -> None:
        """
        Subtract the workload of the PalletRequest to the total workload of the PickingCell
        """
        raise NotImplementedError

    @as_process
    def put(self, *, pallet_request: PalletRequest) -> ProcessGenerator:
        """
        Manage the assignment of a PalletRequest to the PickingCell.

        Adds the workload of the PalletRequest to the workload of the PickingCell.
        Registers the PalletRequest in the PickingCell.
        Stores the PalletRequest in the input queue for later processing.

        Args:
            pallet_request (PalletRequest): PalletRequest to be handled by the PickingCell.
        """

        self.add_workload(job=pallet_request)

        # Register the PalletRequest in the PickingCell
        self.register_pallet_request(pallet_request=pallet_request)

        # Store the PalletRequest in the input queue for later processing
        yield self.input_queue.put(pallet_request)

        return None

    @as_process
    def get(self, pallet_request: PalletRequest) -> ProcessGenerator:
        """
        Retrieves a completed PalletRequest.
        """

        yield self.output_queue.get(lambda pr: pr is pallet_request)
        return pallet_request

    def process_job(self, job: Job | PalletRequest) -> ProcessGenerator:
        raise NotImplementedError

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

        # Eternal process
        while True:
            # Wait for a PalletRequest to be handled
            pallet_request = cast(PalletRequest, (yield self.input_queue.get()))
            pallet_request.started()
            self.current_pallet_request = pallet_request

            with self.building_point.request() as building_point_request:
                # Wait for the availability of the BuildingPoint
                yield building_point_request

                # Process the PalletRequest
                yield self.process_job(pallet_request)

                # Once finished, positions the completed PalletRequest in the output queue of the cell
                yield self.output_queue.put(pallet_request)

                # Housekeeping
                self.pallet_requests_done.append(pallet_request)

                self.remove_workload(job=pallet_request)

                pallet_request.completed()
                self._productivity_history.append((self.system.env.now, self.productivity))

                # Ask the System to handle the retrieval of the finished PalletRequest
                self.system.retrieve_from_cell(cell=self, pallet_request=pallet_request)

    def summary(self, plot=True):
        print(f"## Performance Summary of {self}")

        hourly_cell_productivity = self.productivity * 60 * 60
        hourly_cases_productivity = sum(pallet_request.n_cases for pallet_request in self.pallet_requests_done) / (
            self.system.env.now / 60 / 60
        )
        hourly_layers_productivity = sum(
            len(pallet_request.sub_jobs) for pallet_request in self.pallet_requests_done
        ) / (self.system.env.now / 60 / 60)

        oos_delays = [
            pallet_request.oos_delay for pallet_request in self.pallet_requests_assigned if pallet_request.oos_delay > 0
        ]

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
            [
                "Out of Sequence Delay",
                f"{sum(oos_delays) / 3600:.2f}",
                "[h]",
            ],
        ]
        print(tabulate(table, headers=headers, tablefmt="fancy_grid"))

        if plot:
            import matplotlib.pyplot as plt

            print("## Robot")
            self.robot.plot()
            print("## Aree logiche/fisiche")
            self.feeding_area.plot()
            self.staging_area.plot()
            self.internal_area.plot()

            q = [
                (t, max(q for _, q in qs))
                for t, qs in groupby(self.staging_observer.waiting_fos._history, key=lambda x: x[0])
            ]
            x = [t / 3600 for t, _ in q]
            y = [p for _, p in q]
            plt.plot(x, y)
            plt.title(f"Waiting FOs {self}")
            plt.show()

            plt.plot(oos_delays)
            plt.title(f"Out of Sequence Delays [s] {self}")
            plt.show()
            plt.hist(oos_delays)
            plt.title(f"Out of Sequence Delays distribution [s] {self}")
            plt.show()
