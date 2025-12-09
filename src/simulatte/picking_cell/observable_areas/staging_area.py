from __future__ import annotations

from typing import TYPE_CHECKING, cast

from simulatte.environment import Environment
from simulatte.observables.observable_area.base import ObservableArea

if TYPE_CHECKING:
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.picking_cell.cell import PickingCell
else:

    class PickingCell:  # pragma: no cover
        """Runtime placeholder to satisfy forward references."""

        ...


class StagingArea(ObservableArea["FeedingOperation", "PickingCell"]):
    """
    Represent the logical area inside a picking cell where AGVs wait to be processed.
    """

    def __init__(self, *, capacity: int, owner: PickingCell, signal_at, env: Environment):
        super().__init__(capacity=capacity, owner=owner, signal_at=signal_at, env=env)

    def append(self, item, /):  # type: ignore[override]
        fo = cast("FeedingOperation", item)
        fo.enter_staging_area()
        return super().append(fo)

    def append_exceed(self, item, /):  # type: ignore[override]
        fo = cast("FeedingOperation", item)
        fo.enter_staging_area()
        return super().append_exceed(fo)
