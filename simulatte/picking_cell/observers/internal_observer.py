from __future__ import annotations

from typing import TYPE_CHECKING

from ..areas import InternalArea
from .base_observer import Observer

if TYPE_CHECKING:
    from simulatte.operations import FeedingOperation


class InternalObserver(Observer[InternalArea]):
    def next(self) -> FeedingOperation | None:
        return min(self.observable_area.cell.staging_area, default=None)

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

        if (
            not self.cell.staging_area.is_empty
            and not self.cell.internal_area.is_full
            and (feeding_operation := self.next()) is not None
        ):
            # Remove the FeedingOperation from the StagingArea
            self.cell.staging_area.remove(feeding_operation)

            # Register the FeedingOperation into the InternalArea
            self.cell.internal_area.append(feeding_operation)

            feeding_operation.ant.enter_internal_area()

            # Start moving the ant to the unloading position
            self.cell.let_ant_in(feeding_operation=feeding_operation)
