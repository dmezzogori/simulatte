from __future__ import annotations

from simulatte.observables.observer.base import Observer
from simulatte.operations.feeding_operation import FeedingOperation
from simulatte.picking_cell.observable_areas.staging_area import StagingArea


class StagingObserver(Observer[StagingArea]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.out_of_sequence = set()

    def next(self) -> FeedingOperation | None:
        """
        Select the FeedingOperation allowed to exit the FeedingArea and enter the StagingArea.
        """

        feeding_operations = (
            feeding_operation
            for feeding_operation in self.observable_area.owner.feeding_area
            if feeding_operation.is_in_front_of_staging_area
        )
        return min(feeding_operations, default=None)

    def _can_enter(self, *, feeding_operation: FeedingOperation) -> bool:
        """
        Check if the feeding operation can enter the staging area.

        The feeding operation can enter the staging area if the staging area is not full and the feeding operation is in
        front of the staging area.
        """

        cell = self.observable_area.owner

        is_first_ever_feeding_operation = cell.internal_area.last_in is None and feeding_operation is min(
            cell.feeding_operations
        )
        is_next_useful_feeding_operation = (
            cell.internal_area.last_in is not None
            and feeding_operation.relative_id == cell.internal_area.last_in.relative_id + 1
        )

        return is_first_ever_feeding_operation or is_next_useful_feeding_operation

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

        if (
            not cell.feeding_area.is_empty
            and not cell.staging_area.is_full
            and (feeding_operation := self.next()) is not None
        ):
            if self._can_enter(feeding_operation=feeding_operation):
                feeding_operation.move_into_staging_area()
            else:
                self.out_of_sequence.add(feeding_operation.id)
