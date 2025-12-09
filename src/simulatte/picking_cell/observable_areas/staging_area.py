from __future__ import annotations

from simulatte.observables.observable_area.base import ObservableArea


class StagingArea(ObservableArea["FeedingOperation", "PickingCell"]):
    """
    Represent the logical area inside a picking cell where AGVs wait to be processed.
    """

    def append(self, item):
        item.enter_staging_area()
        return super().append(item)

    def append_exceed(self, item):
        item.enter_staging_area()
        return super().append_exceed(item)
