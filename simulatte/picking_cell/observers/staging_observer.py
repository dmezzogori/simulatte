from __future__ import annotations

from simulatte.observables.observer.base import Observer
from simulatte.operations.feeding_operation import FeedingOperation
from simulatte.picking_cell.observable_areas.staging_area import StagingArea


class StagingObserver(Observer[StagingArea]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.out_of_sequence = set()
        self.first_fo_entered = False
        self.waiting_fos = []

    def next(self) -> FeedingOperation | None:
        """
        Select the FeedingOperation allowed to exit the FeedingArea and enter the StagingArea.
        """

        picking_cell = self.observable_area.owner

        feeding_operations = (
            feeding_operation
            for feeding_operation in picking_cell.feeding_area
            if feeding_operation.is_in_front_of_staging_area and self._can_enter(feeding_operation=feeding_operation)
        )
        return min(feeding_operations, default=None)

    def _can_enter(self, *, feeding_operation: FeedingOperation) -> bool:
        """
        Check if the feeding operation can enter the staging area.

        The feeding operation can enter the staging area if the staging area is not full and the feeding operation is in
        front of the staging area.
        """

        if not self.first_fo_entered:
            entered = self.observable_area.owner.feeding_area[0] == feeding_operation
            if entered:
                self.first_fo_entered = True
                return True
            else:
                return False

        last_in: FeedingOperation = self.observable_area.last_in

        is_first_ever_feeding_operation = last_in is None
        if is_first_ever_feeding_operation:
            return True

        next_useful_product_requests = {product_request.next for product_request in last_in.product_requests}
        for product_request in feeding_operation.product_requests:
            if product_request in next_useful_product_requests:
                return True

        common_product_requests = set(last_in.product_requests).intersection(set(feeding_operation.product_requests))
        if common_product_requests:
            return True

        return False

    def _main_process(self):
        """
        Manage the entering processes of a picking cell.

        Called when the StagingArea signal event is triggered (see WMS._feeding_process).

        It checks if the feeding area is not empty and the staging area is not full.

        The procedure follows the following steps:
        1. Removes the feeding operation from the feeding area.
        2. Moves the feeding operation to the staging area.
        3. Updates the status of the feeding operation.
        4. Signal the feeding area that a feeding operation has been removed.
        5. Signal the internal area that a new feeding operation has entered the staging area.
        """

        cell = self.observable_area.owner

        if cell.feeding_area.is_empty or cell.staging_area.is_full:
            return

        waiting_fos = sum(fo.is_in_front_of_staging_area for fo in cell.feeding_area)
        self.waiting_fos.append(waiting_fos)

        next_feeding_operation = self.next()

        if next_feeding_operation is not None:
            next_feeding_operation.move_into_staging_area()
        else:
            self.out_of_sequence.add(self.observable_area.owner.feeding_area.last_in.id)
