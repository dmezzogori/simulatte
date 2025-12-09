from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

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
from simulatte.protocols.job import Job
from simulatte.reporting import render_table
from simulatte.requests import PalletRequest
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
        self.feeding_area = FeedingArea(capacity=feeding_area_capacity, owner=self, env=self.env)

        # List of FeedingOperation which are currently in the staging area of the PickingCell
        self.staging_area = StagingArea(capacity=staging_area_capacity, owner=self, env=self.env)

        # List of FeedingOperation which are currently in the internal area of the PickingCell
        self.internal_area = InternalArea(capacity=internal_area_capacity, owner=self, env=self.env)

        # List of PalletRequest to be handled by the PickingCell
        self.pallet_requests_assigned: list[PalletRequest] = []

        # List of PalletRequest completed by the PickingCell
        self.pallet_requests_done: list[PalletRequest] = []

        # Current PalletRequest being processed by the PickingCell
        self.current_pallet_request: PalletRequest | None = None

        self._productivity_history: list[tuple[float, float]] = []

        self.workload: float = 0
        self.workload_unit = workload_unit

        self._main: Process | None = None
        if register_main_process:
            self._main = self.main()
            self.starvation_check()

    # --- Lightweight flow control helpers -------------------------------------------------
    def on_feeding_arrival(self, feeding_operation: FeedingOperation) -> None:
        """
        Called when an AGV reaches the cell. Ensures the operation is tracked
        and tries to move it through staging/internal areas if space is available.
        """

        if feeding_operation not in self.feeding_area:
            self.feeding_area.append_exceed(feeding_operation)
        self._pump_feeding_pipeline()

    def on_internal_exit(self) -> None:
        """
        Called when an operation leaves the internal area (drop/return).
        """

        self._pump_feeding_pipeline()

    def _pump_feeding_pipeline(self) -> None:
        """
        Try to admit operations into staging and then internal areas until blocked.
        """

        self._shift_feeding_to_staging()
        self._shift_staging_to_internal()

    def _shift_feeding_to_staging(self) -> None:
        if self.staging_area.is_full or self.feeding_area.is_empty:
            return

        candidate = None
        for fo in self.feeding_area:
            if getattr(fo, "is_in_front_of_staging_area", False):
                candidate = fo
                break
        if candidate is None:
            candidate = self.feeding_area[0]

        candidate.move_into_staging_area()

    def _shift_staging_to_internal(self) -> None:
        if self.internal_area.is_full or self.staging_area.is_empty:
            return

        free_unload_position = next((p for p in self.internal_area.unload_positions if not p.busy), None)
        if free_unload_position is None:
            return

        next_feeding_operation = next(
            (fo for fo in self.staging_area if getattr(fo, "is_inside_staging_area", False)), None
        )
        if next_feeding_operation is None:
            return

        next_feeding_operation.pre_unload_position = None
        next_feeding_operation.unload_position = free_unload_position
        next_feeding_operation.move_into_internal_area()

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
        self._pump_feeding_pipeline()

    def register_pallet_request(self, *, pallet_request: PalletRequest) -> None:
        """
        Register a PalletRequest to be handled by the PickingCell.

        Adds the PalletRequest to the assigned PalletRequests.
        """

        # Add the PalletRequest to the assigned PalletRequests
        self.pallet_requests_assigned.append(pallet_request)

    def add_workload(self, *, job: PalletRequest) -> None:
        """
        Add the workload of the PalletRequest to the total workload of the PickingCell
        """
        if self.workload_unit == "cases":
            self.workload += job.n_cases
        else:
            self.workload += job.workload

    def remove_workload(self, *, job: PalletRequest) -> None:
        """
        Subtract the workload of the PalletRequest to the total workload of the PickingCell
        """
        if self.workload_unit == "cases":
            self.workload -= job.n_cases
        else:
            self.workload -= job.workload

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

    def summary(self, *, plot=True, render=True):
        hourly_cell_productivity = self.productivity * 60 * 60
        hourly_cases_productivity = sum(pallet_request.n_cases for pallet_request in self.pallet_requests_done) / (
            self.system.env.now / 60 / 60
        )
        hourly_lines_productivity = sum(
            len(pallet_request.order_lines) for pallet_request in self.pallet_requests_done
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
            ["Produttività Cella", f"{hourly_lines_productivity:.2f}", "[OrderLines/h]"],
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
        ]
        if render:
            render_table(f"Performance Summary of {self}", headers, table)

        if plot:
            import matplotlib.pyplot as plt

            print("## Robot")
            self.robot.plot()
            print("## Aree logiche/fisiche")
            self.feeding_area.plot()
            self.staging_area.plot()
            self.internal_area.plot()
            plt.show()

        return {"headers": headers, "rows": table}
