from __future__ import annotations

from simulatte.operations import FeedingOperation

from .observable_area import ObservableArea


class StagingArea(ObservableArea[FeedingOperation]):
    """
    Represent the logical area inside a picking cell where ants wait to be processed.
    """

    def append(self, feeding_operation: FeedingOperation) -> None:
        super().append(feeding_operation)
        feeding_operation.enter_staging_area()
