from __future__ import annotations

from simulatte.observables.area.base import Area


class FeedingArea(Area):
    """
    Represent the logical area of Feeding Operations currently associated to a picking cell.
    """

    def append(self, item):
        return super().append(item)

    def append_exceed(self, item):
        return super().append_exceed(item)
