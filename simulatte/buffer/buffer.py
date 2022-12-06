from typing import Type

from simpy.resources.store import Store

import simulatte
from simulatte.unitload import PaperSheet, WoodBoard


class EOQBuffer:
    """
    An instance of this class represents a buffer which is managed
    using a reorder level inventory policy.
    """

    def __init__(
        self,
        *,
        items_type: Type[PaperSheet | WoodBoard],
        reorder_level: int,
        eoq: int,
        get_time: int,
        put_time: int,
        capacity: int = float("inf"),
        init: int = 0,
    ):
        self.env = simulatte.Environment()
        self.items_type = items_type
        self.reorder_level = reorder_level
        self.eoq = eoq
        self.get_time = get_time
        self.put_time = put_time

        self.store = Store(self.env, capacity=capacity)

        if init > capacity:
            raise ValueError("Initial value is greater than capacity")

        for _ in range(init):
            self.store.items.append(items_type())

    @property
    def level(self) -> int:
        return len(self.store.items)

    @property
    def capacity(self) -> int:
        return self.store.capacity

    @property
    def need_refill(self) -> bool:
        """
        This property returns True if the buffer need to be filled,
        and False otherwise.
        """
        return self.level < self.reorder_level

    @simulatte.as_process
    def get(self):
        """
        A process to retrieve an item from the buffer.
        """
        item = yield self.store.get()
        yield self.env.timeout(self.get_time)
        return item

    @simulatte.as_process
    def put(self, *, items):
        """
        A process to store some items into the buffer.
        """
        for item in items:
            yield self.env.timeout(self.put_time)
            yield self.store.put(item)
