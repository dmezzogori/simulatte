from __future__ import annotations

from functools import total_ordering
from typing import TYPE_CHECKING

from simulatte.events.event_payload import EventPayload
from simulatte.events.logged_event import LoggedEvent
from simulatte.location import InternalLocation, Location
from simulatte.logger import logger
from simulatte.utils import as_process
from simulatte.utils.identifiable import Identifiable

if TYPE_CHECKING:
    from simulatte.agv.agv import AGV
    from simulatte.picking_cell.cell import PickingCell
    from simulatte.picking_cell.observable_areas.position import Position
    from simulatte.requests import Request
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation
    from simulatte.stores.warehouse_store import WarehouseStore
    from simulatte.unitload.pallet import Pallet


@total_ordering
class FeedingOperation(metaclass=Identifiable):
    """
    Represents a feeding operation assigned by the System to an agv.
    The agv is responsible for retrieving a unit load from a specific store, according to the
    picking request, and then to bring it to the assigned picking cell.
    """

    __slots__ = (
        "id",
        "cell",
        "relative_id",
        "agv",
        "store",
        "location",
        "unit_load",
        "picking_requests",
        "pre_unload_position",
        "unload_position",
        "status",
        "ready",
    )

    id: int
    agv_position_signal: type[Location] = InternalLocation

    def __init__(
        self,
        *,
        cell: PickingCell,
        agv: AGV,
        store: WarehouseStore,
        picking_requests: list[Request],
        location: WarehouseLocation,
        unit_load: Pallet,
    ) -> None:
        self.cell = cell
        self.relative_id = len(self.cell.feeding_operations)
        self.cell.feeding_operations.append(self)

        self.agv = agv
        self.store = store
        self.location = location
        self.unit_load = unit_load
        self.unit_load.feeding_operation = self
        self.picking_requests = picking_requests

        self.pre_unload_position: Position | None = None
        self.unload_position: Position | None = None

        self.status = {
            "arrived": False,
            "staging": False,
            "inside": False,
            "ready": False,
            "done": False,
        }
        self.ready = LoggedEvent()

    def __str__(self):
        return f"FeedingOperation{self.id}"

    def __lt__(self, other: FeedingOperation) -> bool:
        return self.id < other.id

    def __eq__(self, other: FeedingOperation) -> bool:
        return self.id == other.id

    def _check_status(self, *status_to_be_true) -> bool:
        for status in self.status:
            if status in status_to_be_true:
                if not self.status[status]:
                    return False
            else:
                if self.status[status]:
                    return False
        return True

    @property
    def is_in_front_of_staging_area(self) -> bool:
        return self._check_status("arrived")

    @property
    def is_inside_staging_area(self) -> bool:
        return self._check_status("arrived", "staging")

    @property
    def is_in_internal_area(self) -> bool:
        return self._check_status("arrived", "staging", "inside")

    @property
    def is_at_unload_position(self) -> bool:
        return self._check_status("arrived", "staging", "inside", "ready")

    @property
    def is_done(self) -> bool:
        return self._check_status("arrived", "staging", "inside", "ready", "done")

    def enter_staging_area(self) -> None:
        self.status["staging"] = True

    def enter_internal_area(self) -> None:
        self.status["inside"] = True

    def ready_for_unload(self) -> None:
        self.status["ready"] = True
        self.ready.succeed(value=EventPayload(operation=self, message=f"{self} - Ready for unload"))

    def unloaded(self) -> None:
        self.status["done"] = True

    def move_agv(self, location):
        if isinstance(self.agv.current_location, self.agv_position_signal) and isinstance(
            self.agv.current_location.element, self.agv.picking_cell
        ):
            # Signal that the AGV is ready to receive the next FeedingOperation
            logger.debug(f"{self} - Signaling {self.agv} is ready to receive the next FeedingOperation")
            self.cell.system.idle_feeding_agvs.append(self.agv)
        return self.agv.move_to(location=location)

    @as_process
    def start_retrieval_process(self):
        """
        Start the retrieval process of the unit load
        associated to the FeedingOperation
        from the store.
        """

        logger.debug(f"{self} - Starting the retrieval process from {self.store}")
        yield self.store.get(feeding_operation=self)
        logger.debug(f"{self} - Finished the retrieval process from {self.store}")

    @as_process
    def move_agv_to_store(self):
        """
        Move the agv to the store associated to the FeedingOperation.
        """

        logger.debug(f"{self} - Starting the retrieval agv trip using {self.agv} to {self.store}")
        yield self.move_agv(location=self.store.output_location)
        logger.debug(f"{self} - Finished the retrieval agv trip using {self.agv} to {self.store}")

    @as_process
    def load_agv(self):
        """
        Load the unit load associated to the FeedingOperation
        on the agv.
        """

        logger.debug(f"{self} - Loading {self.unit_load} on {self.agv}")
        yield self.store.load_ant(feeding_operation=self)
        logger.debug(f"{self} - Finished loading {self.unit_load} on {self.agv}")

    @as_process
    def move_agv_to_cell(self):
        """
        Move the agv to the picking cell associated to the FeedingOperation.
        """

        def knock(_):
            # Knock on the door of the picking cell
            self.status["arrived"] = True
            self.agv.waiting_to_enter_staging_area()
            # Signal the cell staging area that the feeding operation is ready to enter the cell
            self.cell.staging_area.trigger_signal_event(
                payload=EventPayload(message=f"{self} - In front of the staging area of {self.cell}")
            )

        logger.debug(f"{self} - Starting the agv trip using {self.agv} to {self.cell}")
        proc = self.move_agv(location=self.cell.input_location)
        proc.callbacks.append(knock)

        yield proc
        logger.debug(f"{self} - Finished the agv trip using {self.agv} to {self.cell}")

    @as_process
    def move_into_staging_area(self):
        """
        Move the FeedingOperation from the feeding area of the picking cell
        into the staging area of the picking cell.

        Move the agv associated to the FeedingOperation
        into the staging area of the picking cell.
        """

        # Remove the FeedingOperation from the FeedingArea
        self.cell.feeding_area.remove(self)

        # The FeedingOperation enters the StagingArea
        logger.debug(f"{self} - Moving into {self.cell} staging area")
        yield self.move_agv(location=self.cell.staging_location)
        logger.debug(f"{self} - Finished moving into {self.cell} staging area")

        self.cell.staging_area.append(self)
        self.agv.enter_staging_area()

        # Knock on internal area
        self.cell.internal_area.trigger_signal_event(
            payload=EventPayload(message=f"{self} - Triggering the signal event {self.cell} internal area")
        )

    @as_process
    def move_into_internal_area(self):
        """
        Move the FeedingOperation from the staging area of the picking cell
        into the internal area of the picking cell.

        Move the agv associated to the FeedingOperation
        into the internal area of the picking cell.
        """

        logger.debug(f"{self} - Moving into {self.cell} internal area")

        # Remove the FeedingOperation from the StagingArea
        self.cell.staging_area.remove(self)

        # The FeedingOperation enters the InternalArea
        self.cell.internal_area.append(self)
        self.agv.enter_internal_area()

        # Start moving the agv to the unloading position
        yield self.cell.let_ant_in(feeding_operation=self)

        logger.debug(f"{self} - Finished moving into {self.cell} internal area")

        # Housekeeping
        self.ready_for_unload()

    @as_process
    def return_to_store(self):
        """
        Move the FeedingOperation from the internal area of the picking cell
        back to the store.

        Move the agv associated to the FeedingOperation
        to the InputLocation of the store.
        """

        logger.debug(f"{self} - Initiating backflow to {self.store}")

        # remove the FeedingOperation from the cell internal area
        self.cell.internal_area.remove(self)

        # de-register the FeedingOperation from the unit load
        self.unit_load.feeding_operation = None

        # Move the AGV to the input location of the store
        logger.debug(f"{self} - Moving {self.agv} to {self.store} input location")
        yield self.move_agv(location=self.store.input_location)
        logger.debug(f"{self} - Finished moving {self.agv} to {self.store} input location")

        # When the AGV is in front of the store, trigger the loading process of the store
        logger.debug(f"{self} - Starting unloading in {self.store}")
        yield self.cell.system.stores_controller.load(store=self.store, agv=self.agv)
        logger.debug(f"{self} - Finished backflow to {self.store}")

    @as_process
    def drop(self):
        """
        Alternative to the return_to_store method.
        The FeedingOperation unit load is totally consumed,
        so the FeedingOperation is dropped.

        The AGV is sent to the recharge location.
        """

        logger.debug(f"{self} - Dropping, moving {self.agv} to recharge location")

        # remove the FeedingOperation from the cell internal area
        self.cell.internal_area.remove(self)

        # Move the AGV to the recharge location
        yield self.move_agv(location=self.cell.system.agv_recharge_location)

        logger.debug(f"{self} - Finished dropping, moved {self.agv} to recharge location")

        # Unload the unit load from the AGV
        yield self.agv.unload()
        self.agv.release_current()
