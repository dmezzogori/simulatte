from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import total_ordering
from typing import TYPE_CHECKING

from simulatte.events.event_payload import EventPayload
from simulatte.events.logged_event import LoggedEvent
from simulatte.location import InternalLocation, Location
from simulatte.logger import logger
from simulatte.unitload import PalletSingleProduct
from simulatte.utils.as_process import as_process
from simulatte.utils.env_mixin import EnvMixin
from simulatte.utils.identifiable_mixin import IdentifiableMixin

if TYPE_CHECKING:
    from simulatte.agv.agv import AGV
    from simulatte.picking_cell.cell import PickingCell
    from simulatte.picking_cell.observable_areas.position import Position
    from simulatte.protocols.warehouse_store import WarehouseStoreProtocol
    from simulatte.requests import PalletRequest, ProductRequest
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


@total_ordering
class FeedingOperationLog:
    __slots__ = (
        "feeding_operation",
        "created",
        "started_retrieval",
        "finished_retrieval",
        "started_agv_trip_to_store",
        "finished_agv_trip_to_store",
        "started_loading",
        "finished_loading",
        "started_agv_trip_to_cell",
        "finished_agv_trip_to_cell",
        "started_agv_trip_to_staging_area",
        "finished_agv_trip_to_staging_area",
        "started_agv_trip_to_internal_area",
        "finished_agv_trip_to_internal_area",
        "started_agv_return_trip_to_store",
        "finished_agv_return_trip_to_store",
        "started_agv_unloading_for_return_trip_to_store",
        "finished_agv_unloading_for_return_trip_to_store",
        "started_agv_return_trip_to_recharge",
        "finished_agv_return_trip_to_recharge",
    )

    def __init__(self, feeding_operation: FeedingOperation, created: float):
        self.feeding_operation = feeding_operation
        self.created = created

        self.started_retrieval: float | None = None
        self.finished_retrieval: float | None = None

        self.started_agv_trip_to_store: float | None = None
        self.finished_agv_trip_to_store: float | None = None

        self.started_loading: float | None = None
        self.finished_loading: float | None = None

        self.started_agv_trip_to_cell: float | None = None
        self.finished_agv_trip_to_cell: float | None = None

        self.started_agv_trip_to_staging_area: float | None = None
        self.finished_agv_trip_to_staging_area: float | None = None

        self.started_agv_trip_to_internal_area: float | None = None
        self.finished_agv_trip_to_internal_area: float | None = None

        self.started_agv_return_trip_to_store: float | None = None
        self.finished_agv_return_trip_to_store: float | None = None

        self.started_agv_unloading_for_return_trip_to_store: float | None = None
        self.finished_agv_unloading_for_return_trip_to_store: float | None = None

        self.started_agv_return_trip_to_recharge: float | None = None
        self.finished_agv_return_trip_to_recharge: float | None = None

    def __lt__(self, other) -> bool:
        if not isinstance(other, FeedingOperationLog):
            return NotImplemented
        return self.created < other.created

    def __eq__(self, other) -> bool:
        if not isinstance(other, FeedingOperationLog):
            return NotImplemented
        return self.created == other.created

    @property
    def feeding_operation_starts(self):
        if self.started_agv_trip_to_store is None:
            return None
        return self.started_agv_trip_to_store - self.created

    @property
    def agv_move_to_store(self):
        if self.finished_agv_trip_to_store is None:
            return None
        return self.finished_agv_trip_to_store - self.started_agv_trip_to_store

    @property
    def agv_waiting_at_store(self) -> float | None:
        if self.started_loading is None:
            return None
        return self.started_loading - self.finished_agv_trip_to_store

    @property
    def agv_move_from_store_to_cell(self):
        if self.finished_agv_trip_to_cell is None:
            return None
        return self.finished_agv_trip_to_cell - self.started_agv_trip_to_cell

    @property
    def agv_waiting_at_cell(self):
        if self.started_agv_trip_to_staging_area is None:
            return None
        return self.started_agv_trip_to_staging_area - self.finished_agv_trip_to_cell

    @property
    def agv_waiting_at_staging(self):
        if self.started_agv_trip_to_internal_area is None:
            return None
        return self.started_agv_trip_to_internal_area - self.finished_agv_trip_to_staging_area

    @property
    def agv_move_from_staging_to_internal(self):
        if self.finished_agv_trip_to_internal_area is None:
            return None
        return self.finished_agv_trip_to_internal_area - self.started_agv_trip_to_internal_area

    @property
    def agv_waiting_at_internal(self):
        if self.started_agv_return_trip_to_store is not None:
            return self.started_agv_return_trip_to_store - self.finished_agv_trip_to_internal_area
        if self.started_agv_return_trip_to_recharge is None:
            return None
        return self.started_agv_return_trip_to_recharge - self.finished_agv_trip_to_internal_area

    @property
    def feeding_operation_life_time(self):
        if self.finished_agv_unloading_for_return_trip_to_store is not None:
            return self.finished_agv_unloading_for_return_trip_to_store - self.created
        if self.finished_agv_return_trip_to_recharge is None:
            return None
        return self.started_agv_return_trip_to_recharge - self.created

    def to_tuple(self):
        return (
            (self.created, "created", self.feeding_operation),
            (self.started_retrieval, "started_retrieval", self.feeding_operation),
            (self.finished_retrieval, "finished_retrieval", self.feeding_operation),
            (self.started_agv_trip_to_store, "started_agv_trip_to_store", self.feeding_operation),
            (self.finished_agv_trip_to_store, "finished_agv_trip_to_store", self.feeding_operation),
            (self.started_loading, "started_loading", self.feeding_operation),
            (self.finished_loading, "finished_loading", self.feeding_operation),
            (self.started_agv_trip_to_cell, "started_agv_trip_to_cell", self.feeding_operation),
            (self.finished_agv_trip_to_cell, "finished_agv_trip_to_cell", self.feeding_operation),
            (self.started_agv_trip_to_staging_area, "started_agv_trip_to_staging_area", self.feeding_operation),
            (self.finished_agv_trip_to_staging_area, "finished_agv_trip_to_staging_area", self.feeding_operation),
            (self.started_agv_trip_to_internal_area, "started_agv_trip_to_internal_area", self.feeding_operation),
            (self.finished_agv_trip_to_internal_area, "finished_agv_trip_to_internal_area", self.feeding_operation),
            (self.started_agv_return_trip_to_store, "started_agv_return_trip_to_store", self.feeding_operation),
            (self.finished_agv_return_trip_to_store, "finished_agv_return_trip_to_store", self.feeding_operation),
            (
                self.started_agv_unloading_for_return_trip_to_store,
                "started_agv_unloading_for_return_trip_to_store",
                self.feeding_operation,
            ),
            (
                self.finished_agv_unloading_for_return_trip_to_store,
                "finished_agv_unloading_for_return_trip_to_store",
                self.feeding_operation,
            ),
            (self.started_agv_return_trip_to_recharge, "started_agv_return_trip_to_recharge", self.feeding_operation),
            (self.finished_agv_return_trip_to_recharge, "finished_agv_return_trip_to_recharge", self.feeding_operation),
        )

    def check(self):
        if self.finished_retrieval <= self.started_retrieval:
            raise ValueError("Retrieval process not consistent")
        if self.finished_agv_trip_to_store <= self.started_agv_trip_to_store:
            raise ValueError("AGV trip to store not consistent")
        if self.finished_loading <= self.started_loading:
            raise ValueError("Loading process not consistent")
        if self.finished_agv_trip_to_cell <= self.started_agv_trip_to_cell:
            raise ValueError("AGV trip to cell not consistent")
        if self.finished_agv_trip_to_staging_area <= self.started_agv_trip_to_staging_area:
            raise ValueError("AGV trip to staging area not consistent")
        if self.finished_agv_trip_to_internal_area <= self.started_agv_trip_to_internal_area:
            raise ValueError("AGV trip to internal area not consistent")
        if self.finished_agv_return_trip_to_store <= self.started_agv_return_trip_to_store:
            raise ValueError("AGV return trip to store not consistent")
        if self.finished_agv_unloading_for_return_trip_to_store <= self.started_agv_unloading_for_return_trip_to_store:
            raise ValueError("AGV unloading for return trip to store not consistent")
        if self.finished_agv_return_trip_to_recharge <= self.started_agv_return_trip_to_recharge:
            raise ValueError("AGV return trip to recharge not consistent")


@total_ordering
class FeedingOperation(IdentifiableMixin, EnvMixin):
    """
    Represents a feeding operation assigned by the System to an agv.
    The agv is responsible for retrieving a unit load from a specific store, according to the
    picking request, and then to bring it to the assigned picking cell.
    """

    __slots__ = (
        "id",
        "env",
        "cell",
        "relative_id",
        "agv",
        "store",
        "location",
        "unit_load",
        "product_requests",
        "pre_unload_position",
        "unload_position",
        "status",
        "ready",
        "log",
    )

    agv_position_signal: type[Location] = InternalLocation

    def __init__(
        self,
        *,
        cell: PickingCell,
        agv: AGV,
        store: WarehouseStoreProtocol,
        product_requests: Sequence[ProductRequest],
        location: WarehouseLocation,
        unit_load: PalletSingleProduct,
    ) -> None:
        IdentifiableMixin.__init__(self)
        EnvMixin.__init__(self)

        self.cell = cell
        self.relative_id = len(self.cell.feeding_operations)
        self.cell.feeding_operations.append(self)

        self.agv = agv
        self.store = store
        self.location = location
        self.unit_load = unit_load
        self.has_partial_unit_load = False
        if len(self.unit_load.layers) == 1:
            self.has_partial_unit_load = self.unit_load.upper_layer.n_cases < self.unit_load.product.cases_per_layer
        else:
            self.has_partial_unit_load = len(self.unit_load.layers) < self.unit_load.product.layers_per_pallet
        self.unit_load.feeding_operation = self
        self.product_requests = product_requests
        for product_request in self.product_requests:
            product_request.feeding_operations.append(self)

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

        self.log = FeedingOperationLog(self, self.env.now)

        self.opportunistic = False

    def __lt__(self, other) -> bool:
        if not isinstance(other, FeedingOperation):
            return NotImplemented
        return self.id < other.id

    def __eq__(self, other) -> bool:
        if not isinstance(other, FeedingOperation):
            return NotImplemented
        return self.id == other.id

    @property
    def pallet_requests(self) -> set[PalletRequest]:
        """
        Return the set of pallet requests associated to the FeedingOperation.
        """
        return {product_request.parent.parent for product_request in self.product_requests}

    @property
    def chain(self) -> Iterable[FeedingOperation]:
        """
        Return the chain of feeding operations that are associated to the same pallet requests.
        """
        for feeding_operation in self.cell.feeding_operations:
            if feeding_operation.pallet_requests.intersection(self.pallet_requests):
                yield feeding_operation

    def _check_status(self, *status_to_be_true) -> bool:
        for status in self.status:
            if status in status_to_be_true:
                if not self.status[status]:
                    return False
            else:
                if self.status[status]:
                    return False
        return True

    def release_unload_position(self) -> None:
        if self.unload_position is None:
            raise ValueError("Unload position not assigned")

        self.unload_position.release_current()

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

    def move_agv(self, location, skip_idle_signal=False):
        if (
            isinstance(self.agv.current_location, self.agv_position_signal)
            and isinstance(self.agv.current_location.element, self.agv.picking_cell)
            and not skip_idle_signal
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
        self.log.started_retrieval = self.env.now
        yield self.store.get(feeding_operation=self)
        self.log.finished_retrieval = self.env.now
        logger.debug(f"{self} - Finished the retrieval process from {self.store}")

    @as_process
    def move_agv_to_store(self):
        """
        Move the agv to the store associated to the FeedingOperation.
        """

        logger.debug(f"{self} - Starting the retrieval agv trip using {self.agv} to {self.store}")
        self.log.started_agv_trip_to_store = self.env.now
        yield self.move_agv(location=self.store.output_location)
        self.store.output_agvs_queue += 1
        self.store.output_agvs_queue_history.append((self.env.now, self.store.output_agvs_queue))
        self.log.finished_agv_trip_to_store = self.env.now
        logger.debug(f"{self} - Finished the retrieval agv trip using {self.agv} to {self.store}")

    @as_process
    def load_agv(self):
        """
        Load the unit load associated to the FeedingOperation
        on the agv.
        """

        logger.debug(f"{self} - Loading {self.unit_load} on {self.agv}")
        self.log.started_loading = self.env.now
        yield self.store.load_agv(feeding_operation=self)
        # Update the output AGVs queue
        self.store.output_agvs_queue -= 1
        self.store.output_agvs_queue_history.append((self.env.now, self.store.output_agvs_queue))
        self.log.finished_loading = self.env.now
        logger.debug(f"{self} - Finished loading {self.unit_load} on {self.agv}")

    @as_process
    def move_agv_to_cell(self, skip_idle_signal=False):
        """
        Move the agv to the picking cell associated to the FeedingOperation.
        """

        logger.debug(f"{self} - Starting the agv trip using {self.agv} to {self.cell}")
        self.log.started_agv_trip_to_cell = self.env.now
        yield self.move_agv(location=self.cell.input_location, skip_idle_signal=skip_idle_signal)
        self.log.finished_agv_trip_to_cell = self.env.now

        # Knock on the door of the picking cell
        self.status["arrived"] = True
        # Signal the cell staging area that the feeding operation is ready to enter the cell
        self.cell.staging_area.trigger_signal_event(
            payload=EventPayload(message=f"{self} - In front of the staging area of {self.cell}")
        )

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
        self.log.started_agv_trip_to_staging_area = self.env.now
        yield self.move_agv(location=self.cell.staging_location)
        self.log.finished_agv_trip_to_staging_area = self.env.now
        logger.debug(f"{self} - Finished moving into {self.cell} staging area")

        self.cell.staging_area.append(self, exceed=True)

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

        if self.pre_unload_position is not None:
            pre_unload_position_request = self.pre_unload_position.request(operation=self)
            logger.debug(f"{self} - Waiting for pre-unload position request")
            yield pre_unload_position_request
            logger.debug(f"{self} - Pre-unload position request granted")

            # Move the Ant from the StagingArea to the InternalArea
            self.log.started_agv_trip_to_internal_area = self.env.now
            yield self.move_agv(location=self.cell.internal_location)
            self.log.finished_agv_trip_to_internal_area = self.env.now
            # The FeedingOperation enters the InternalArea
            self.cell.internal_area.append(self)
            logger.debug(f"{self} - Finished moving into {self.cell} internal area")

            # Wait for the assigned InternalArea UnloadPosition to be free
            unload_position_request = self.unload_position.request(operation=self)
            logger.debug(f"{self} - Waiting for unload position request")
            yield unload_position_request
            logger.debug(f"{self} - Unload position request granted")

            self.pre_unload_position.release(pre_unload_position_request)
        else:
            unload_position_request = self.unload_position.request(operation=self)
            logger.debug(f"{self} - Waiting for unload position request")
            yield unload_position_request
            logger.debug(f"{self} - Unload position request granted")

            # Move the Ant from the StagingArea to the InternalArea
            self.log.started_agv_trip_to_internal_area = self.env.now
            yield self.move_agv(location=self.cell.internal_location)
            self.log.finished_agv_trip_to_internal_area = self.env.now
            # The FeedingOperation enters the InternalArea
            self.cell.internal_area.append(self)
            logger.debug(f"{self} - Finished moving into {self.cell} internal area")

        # Housekeeping
        self.ready_for_unload()

    @as_process
    def return_to_store(self, store, location, priority: int):
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
        self.log.started_agv_return_trip_to_store = self.env.now
        yield self.move_agv(location=store.input_location)
        store.input_agvs_queue += 1
        store.input_agvs_queue_history.append((self.env.now, store.input_agvs_queue))
        self.log.finished_agv_return_trip_to_store = self.env.now
        logger.debug(f"{self} - Finished moving {self.agv} to {store} input location")

        # When the AGV is in front of the store, trigger the loading process of the store
        logger.debug(f"{self} - Starting unloading in {store}")
        self.log.started_agv_unloading_for_return_trip_to_store = self.env.now
        yield store.put(unit_load=self.agv.unit_load, location=location, agv=self.agv, priority=priority)
        store.input_agvs_queue -= 1
        store.input_agvs_queue_history.append((self.env.now, store.input_agvs_queue))
        self.log.finished_agv_unloading_for_return_trip_to_store = self.env.now
        logger.debug(f"{self} - Finished backflow to {store}")

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
        self.log.started_agv_return_trip_to_recharge = self.env.now
        yield self.move_agv(location=self.store.input_location)
        self.log.finished_agv_return_trip_to_recharge = self.env.now

        logger.debug(f"{self} - Finished dropping, moved {self.agv} to recharge location")

        # Unload the unit load from the AGV
        yield self.agv.unload()
        self.agv.release_current()
