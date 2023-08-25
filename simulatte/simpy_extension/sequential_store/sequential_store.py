from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Generic, TypeVar

import simulatte.utils
from simpy.resources.store import FilterStore, Store

if TYPE_CHECKING:
    from simpy import Environment


T = TypeVar("T")


class SequentialStore(Generic[T]):
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
        self.get_queue = 0

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
    def items(self) -> Sequence[T]:
        return self._output.items + self._internal_store.items

    @simulatte.utils.as_process
    def put(self, item: T):
        if self.output_level == 0:
            yield self._output.put(item)
        else:
            yield self._internal_store.put(item)

    @simulatte.utils.as_process
    def get(self, filter_: Callable) -> T:
        # Get the item from the output position
        item = yield self._output.get(filter_)

        # Eventually move the next item in the internal store to the output position
        if self.internal_store_level > 0:
            next_item = yield self._internal_store.get()
            yield self._output.put(next_item)

        # return retrieved item
        return item
