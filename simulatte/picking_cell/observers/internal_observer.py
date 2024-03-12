from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.events.event_payload import EventPayload
from simulatte.observables.observer.base import Observer
from simulatte.picking_cell.observable_areas.internal_area import InternalArea

if TYPE_CHECKING:
    from simulatte.operations.feeding_operation import FeedingOperation


class InternalObserver(Observer[InternalArea]):
    def next(self) -> FeedingOperation | None:
        picking_cell = self.observable_area.owner

        feeding_operations = (
            feeding_operation
            for feeding_operation in picking_cell.staging_area
            if feeding_operation.is_inside_staging_area and self._can_enter(feeding_operation=feeding_operation)
        )

        return min(feeding_operations, default=None)

    def _can_enter(self, *, feeding_operation: FeedingOperation) -> bool:
        last_in: FeedingOperation = self.observable_area.last_in
        last_out: FeedingOperation = self.observable_area.last_out

        is_first_ever_feeding_operation = last_in is None
        if is_first_ever_feeding_operation:
            return True

        next_useful_product_requests_from_last_in = {
            product_request.next for product_request in last_in.product_requests
        }

        next_useful_product_requests_from_last_out = set()
        if last_out is not None:
            next_useful_product_requests_from_last_out = {
                product_request.next for product_request in last_out.product_requests
            }

        next_useful_product_requests = next_useful_product_requests_from_last_in.union(
            next_useful_product_requests_from_last_out
        )

        for product_request in feeding_operation.product_requests:
            if product_request in next_useful_product_requests:
                return True

        common_product_requests = set(last_in.product_requests).intersection(set(feeding_operation.product_requests))
        if common_product_requests:
            return True

        return False

    def _main_process(self) -> None:
        """
        Manage the shift of a FeedingOperation from the StagingArea to the InternalArea.

        Called when the InternalArea signal event is triggered (see StagingObserver._main_process).

        It checks if the staging area is not empty and the internal area is not full.

        If all conditions are met, the procedure follows the following steps:
        1. Removes the FeedingOperation from the StagingArea.
        2. Register the FeedingOperation into the InternalArea.
        3. Updates the status of the FeedingOperation.
        4. Initialize the process that will take care of the Ant logistic movements.
        """

        cell = self.observable_area.owner

        if cell.internal_area.is_full or cell.staging_area.is_empty:
            return

        for unload_position in self.observable_area.unload_positions:
            if not unload_position.busy:
                free_unload_position = unload_position
                break
        else:
            return

        next_feeding_operation = self.next()

        if next_feeding_operation is not None:
            next_feeding_operation.pre_unload_position = None
            next_feeding_operation.unload_position = free_unload_position

            next_feeding_operation.move_into_internal_area()

        else:
            self.observable_area.owner.staging_area.trigger_signal_event(
                payload=EventPayload(
                    message="TRIGGERING STAGING AREA SIGNAL EVENT FROM INTERNAL OBSERVER",
                )
            )
