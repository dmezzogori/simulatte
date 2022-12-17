from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter

if TYPE_CHECKING:
    from simulatte.picking_cell import PickingCell


T = TypeVar("T")


class Area(list, Generic[T]):
    """
    Implement a virtual area of interest for a picking cell.
    Extends list to add finite capacity.
    """

    def __init__(self, *, cell: PickingCell, capacity: int = float("inf")) -> None:
        super().__init__()
        self.env = cell.system.env
        self.cell = cell
        self.capacity = capacity
        self._history = []
        self.last_entered: T = None

    def pop(self, index: int = -1) -> T:
        item = super().pop(index)
        self._history.append((self.env.now, len(self)))
        return item

    def remove(self, item: T) -> None:
        super().remove(item)
        self._history.append((self.env.now, len(self)))

    @property
    def is_full(self) -> bool:
        return len(self) == self.capacity

    @property
    def is_empty(self) -> bool:
        return len(self) == 0

    @property
    def free_space(self) -> int:
        return self.capacity - len(self)

    def append(self, item: T) -> None:
        if self.is_full:
            raise ValueError("Area is full.")

        super().append(item)
        self.last_entered = item
        self._history.append((self.env.now, len(self)))

    def plot(self):
        x = [t / 60 / 60 for t, _ in self._history]
        y = [s for _, s in self._history]
        plt.plot(x, y)
        plt.yticks(range(0, max(y) + 1))
        plt.xlabel("Time [h]")
        plt.ylabel(f"Queue [#items]")
        plt.title(f"{self.__class__.__name__} queue")
        plt.show()
