from __future__ import annotations

from typing import TYPE_CHECKING, cast

from simulatte.observables.observable_area.base import ObservableArea

if TYPE_CHECKING:
    from simulatte.operations.feeding_operation import FeedingOperation


class StagingArea(ObservableArea["FeedingOperation", "PickingCell"]):
    """
    Represent the logical area inside a picking cell where AGVs wait to be processed.
    """

    def append(self, item, /):  # type: ignore[override]
        fo = cast("FeedingOperation", item)
        fo.enter_staging_area()
        return super().append(fo)

    def append_exceed(self, item, /):  # type: ignore[override]
        fo = cast("FeedingOperation", item)
        fo.enter_staging_area()
        return super().append_exceed(fo)
