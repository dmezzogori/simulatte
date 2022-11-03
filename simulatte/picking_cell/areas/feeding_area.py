from __future__ import annotations

from .observable_area import ObservableArea
from ...operations import FeedingOperation


class FeedingArea(ObservableArea[FeedingOperation]):
    """
    Represent the logical area of feeding ants currently serving a picking cell.
    """

    pass
