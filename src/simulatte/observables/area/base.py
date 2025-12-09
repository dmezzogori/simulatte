from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from simulatte.typings import History
from simulatte.environment import Environment
from simulatte.utils import EnvMixin

if TYPE_CHECKING:
    pass


Item = TypeVar("Item")
Owner = TypeVar("Owner")


class Area[Item, Owner](list[Item], EnvMixin):
    """
    Implement a virtual area.
    Extends list to add (optional) finite capacity.
    Records the content history, for statistics and plotting.
    """

    __slots__ = ("env", "capacity", "_history", "last_in", "last_out")

    def __init__(self, *, owner: Owner, env: Environment, capacity: float = float("inf")) -> None:
        EnvMixin.__init__(self, env=env)
        list.__init__(self)

        self.capacity = capacity
        self.owner = owner

        self._history: History[int] = []
        self.last_in: object | None = None
        self.last_out: object | None = None

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

    def append(self, item: object, /) -> None:  # type: ignore[override]
        """Append respecting capacity; record history."""

        if self.is_full:
            raise RuntimeError("Area is full.")

        self.last_in = item
        self._history.append((self.env.now, len(self)))

        super().append(item)

    def append_exceed(self, item: object, /) -> None:
        """Append ignoring capacity limits; record history."""

        self.last_in = item
        self._history.append((self.env.now, len(self)))

        super().append(item)

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

    def remove(self, item: object, /) -> None:  # type: ignore[override]
        """Remove item; record history."""

        self.last_out = item
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
