from __future__ import annotations

from simulatte.observables.observable_area.base import ObservableArea
from simulatte.operations.feeding_operation import FeedingOperation


class StagingArea(ObservableArea):
    """
    Represent the logical area inside a picking cell where AGVs wait to be processed.
    """

    def append(self, item: FeedingOperation, exceed=False):
        item.enter_staging_area()
        return super().append(item, exceed=exceed)
