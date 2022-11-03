from simulatte.operations import FeedingOperation

from simulatte.logger.logger import EventPayload
from ..areas import StagingArea
from .base_observer import Observer


class StagingObserver(Observer[StagingArea]):
    def next(self) -> FeedingOperation | None:
        """
        Select the FeedingOperation allowed to exit the FeedingArea and enter the StagingArea.
        """
        feeding_operations = (
            feeding_operation
            for feeding_operation in self.observable_area.cell.feeding_area
            if feeding_operation.is_in_front_of_staging_area
        )
        return min(feeding_operations, default=None)

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

        if (
            not self.cell.feeding_area.is_empty
            and not self.cell.staging_area.is_full
            and (feeding_operation := self.next()) is not None
        ):
            if (
                self.cell.internal_area.last_entered is None and feeding_operation is min(self.cell.feeding_operations)
            ) or (
                (
                    self.cell.internal_area.last_entered is not None
                    and feeding_operation.id == self.cell.internal_area.last_entered.id + 1
                )
            ):
                # Remove the FeedingOperation from the FeedingArea
                self.cell.feeding_area.remove(feeding_operation)

                # The FeedingOperation enters the StagingArea
                self.cell.staging_area.append(feeding_operation)

                feeding_operation.ant.move_to(system=self.system, location=self.cell.staging_location)

                # Trigger the InternalArea signal event since a new FeedingOperation is available in the StagingArea
                payload = EventPayload(
                    event="ACTIVATING INTERNAL AREA SIGNAL", type=0, value={"type": 0, "operation": feeding_operation}
                )
                self.cell.internal_area.trigger_signal_event(payload=payload)
