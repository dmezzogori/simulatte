from __future__ import annotations

from typing import TYPE_CHECKING, Sequence, Callable, Any

if TYPE_CHECKING:
    from simpy import Environment

from simpy.resources.store import Store, FilterStore


class SequentialStore:
    """
    The SequentialStore implements a FIFO queue (items are stored in the order in which they are put).

    The SequentialStore is implemented as a combination of a FilterStore and a Store.
    The FilterStore is used to store the next item to be yielded,
    while the Store is used to store the remaining items.

    The retrieved element must satisfy the filter function applied to the FilterStore.
    """

    def __init__(self, env: Environment, capacity: int = float("inf")) -> None:

        if capacity <= 1:
            raise ValueError("Capacity of SequentialStore must be grater than 1.")

        self.env = env
        self._capacity = capacity
        self._internal_store = Store(env, capacity=capacity - 1)
        self._output = FilterStore(env, capacity=1)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def internal_store_level(self) -> int:
        return len(self._internal_store.items)

    @property
    def output_level(self) -> int:
        return len(self._output.items)

    @property
    def level(self) -> int:
        return self.internal_store_level + self.output_level

    @property
    def items(self) -> Sequence:
        return self._output.items + self._internal_store.items

    def _do_put(self, item: Any):
        if self.output_level == 0:
            yield self._output.put(item)
        else:
            yield self._internal_store.put(item)

    def put(self, item):
        return self.env.process(self._do_put(item))

    def _do_get(self, filter_: Callable):
        # Get the item from the output position
        item = yield self._output.get(filter_)

        # Eventually move the next item in the internal store to the output position
        if self.internal_store_level > 0:
            next_item = yield self._internal_store.get()
            yield self._output.put(next_item)

        # return retrieved item
        return item

    def get(self, filter_: Callable):
        return self.env.process(self._do_get(filter_))
