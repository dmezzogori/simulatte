from __future__ import annotations

from simulatte.observables import Area
from simulatte.observables.observer.base import Observer
from simulatte.operations.feeding_operation import FeedingOperation
from simulatte.picking_cell.observable_areas.staging_area import StagingArea


class WaitingAGVsArea(Area):
    pass


class StagingObserver(Observer[StagingArea]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.out_of_sequence = set()
        self.waiting_fos = WaitingAGVsArea(owner=self.observable_area.owner, env=self.env)

    def next(self) -> FeedingOperation | None:
        """
        Select the FeedingOperation allowed to exit the FeedingArea and enter the StagingArea.
        """

        cell = self.observable_area.owner
        for feeding_operation in cell.feeding_area:
            if feeding_operation.is_in_front_of_staging_area:
                return feeding_operation
        return None

    def _can_enter(self, *, feeding_operation: FeedingOperation) -> bool:
        return feeding_operation.is_in_front_of_staging_area

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

        self.waiting_fos.clear()
        for fo in cell.feeding_area:
            if sum(fo.status.values()) == 1:
                self.waiting_fos.append(fo)

        next_feeding_operation = self.next()

        if next_feeding_operation is not None:
            next_feeding_operation.move_into_staging_area()
