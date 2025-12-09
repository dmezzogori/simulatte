from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.simpy_extension.multi_store.multi_store import MultiStore
from simulatte.simpy_extension.sequential_store.sequential_store import SequentialStore

if TYPE_CHECKING:
    pass


class SequentialMultiStore(SequentialStore):
    """
    An instance of this class represents a SequentialStore where is
    possible to put more items at the same time (as in a MultiStore).

    It is not possible to get more elements at the same time because a
    one-piece-flow sequence must be respected.
    """

    def __init__(self, *, env: Environment, capacity: float = float("inf")) -> None:
        super().__init__(env=env, capacity=capacity)
        # Overwrite the store with a MultiStore instance
        self._internal_store = MultiStore(env=env, capacity=capacity - 1)

    def _do_put(self, items: Sequence):
        if len(items) >= self.capacity:
            raise Exception(f"Items to store exceed the capacity {self.capacity}.")

        if self.output_level == 0:
            yield self._output.put(items[0])
            if len(items) > 1:
                yield self._internal_store.put(items[1:])
        else:
            yield self._internal_store.put(items)

    def _do_get(self, filter_: Callable):
        # Get the item from the exit position
        item = yield self._output.get(filter_)

        # Eventually move the next item in the internal store to the output position
        if self.internal_store_level > 0:
            next_item = yield self._internal_store.get(1)
            self._output.put(next_item[0])

        # return retrieved item
        return item
