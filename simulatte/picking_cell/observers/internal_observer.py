from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.events.event_payload import EventPayload
from simulatte.observables.observer.base import Observer
from simulatte.picking_cell.observable_areas.internal_area import InternalArea

if TYPE_CHECKING:
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.picking_cell.observable_areas.position import Position


class InternalObserver(Observer[InternalArea]):
    def next(self) -> FeedingOperation | None:
        return min(self.observable_area.owner.staging_area, default=None)

    def _can_enter(self, *, feeding_operation: FeedingOperation) -> tuple[bool, Position | None, Position | None]:
        for unload_position in self.observable_area.owner.internal_area.unload_positions:
            if not unload_position.busy:
                return True, None, unload_position
        return False, None, None

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

        if not cell.internal_area.is_full:
            if not cell.staging_area.is_empty and (feeding_operation := self.next()) is not None:
                can_enter, pre_unload_position, unload_position = self._can_enter(feeding_operation=feeding_operation)

                if can_enter:
                    feeding_operation.pre_unload_position = pre_unload_position
                    feeding_operation.unload_position = unload_position

                    feeding_operation.move_into_internal_area()

            else:
                self.observable_area.owner.staging_area.trigger_signal_event(
                    payload=EventPayload(
                        message="TRIGGERING STAGING AREA SIGNAL EVENT FROM INTERNAL OBSERVER",
                    )
                )
