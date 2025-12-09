from __future__ import annotations

from simulatte.environment import Environment
from simulatte.observables.area.base import Area


class FeedingArea(Area):
    """
    Represent the logical area of Feeding Operations currently associated to a picking cell.
    """

    def __init__(self, *, capacity: int, owner, env: Environment):
        super().__init__(capacity=capacity, owner=owner, env=env)

    def append(self, item, /):  # type: ignore[override]
        return super().append(item)

    def append_exceed(self, item, /):
        return super().append_exceed(item)
