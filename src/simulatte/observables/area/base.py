from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from simulatte.typings import History
from simulatte.utils import EnvMixin

if TYPE_CHECKING:
    pass


Item = TypeVar("Item")
Owner = TypeVar("Owner")


class Area[Item, Owner](list, EnvMixin):
    """
    Implement a virtual area.
    Extends list to add (optional) finite capacity.
    Records the content history, for statistics and plotting.
    """

    __slots__ = ("env", "capacity", "_history", "last_in", "last_out")

    def __init__(self, *, capacity: float = float("inf"), owner: Owner) -> None:
        EnvMixin.__init__(self)
        list.__init__(self)

        self.capacity = capacity
        self.owner = owner

        self._history: History[int] = []
        self.last_in: Item | None = None
        self.last_out: Item | None = None

    @property
    def is_full(self) -> bool:
        """
        Return True if the area is full, False otherwise.
        """

        return len(self) >= self.capacity

    @property
    def is_empty(self) -> bool:
        """
        Return True if the area is empty, False otherwise.
        """

        return len(self) == 0

    @property
    def free_space(self) -> float:
        """
        Return the free available space in the area.
        """

        return self.capacity - len(self)

    def append(self, item: Item, exceed=False):
        """
        Override the list append method to add finite capacity.
        The finite capacity can be exceeded if the exceed flag is set to True.
        Record the content history, and update the last inserted item.
        """

        # If the area is full and the `exceed` flag is not set, raise an error.
        if self.is_full and not exceed:
            raise RuntimeError("Area is full.")

        # Update the last inserted item.
        self.last_in = item

        # Record the content history.
        self._history.append((self.env.now, len(self)))

        return super().append(item)

    def pop(self, index=-1) -> Item:
        """
        Override the list pop method to record the content history, and update the last removed item.
        """

        # Pop the item.
        item = super().pop(index)

        # Update the last removed item.
        self.last_out = item

        # Record the content history.
        self._history.append((self.env.now, len(self)))

        return item

    def remove(self, item: Item) -> None:
        """
        Override the list remove method to record the content history, and update the last removed item.
        """

        # Update the last removed item.
        self.last_out = item

        # Record the content history.
        self._history.append((self.env.now, len(self)))

        super().remove(item)

    def plot(self):
        """
        Plot the content history.
        """

        import matplotlib.pyplot as plt

        x = [t / 60 / 60 for t, _ in self._history]
        y = [s for _, s in self._history]
        plt.plot(x, y)
        plt.yticks(range(0, max(y) + 1))
        plt.xlabel("Time [h]")
        plt.ylabel("Queue [#items]")
        plt.title(f"{self.owner} {self.__class__.__name__} queue")
        plt.show()
