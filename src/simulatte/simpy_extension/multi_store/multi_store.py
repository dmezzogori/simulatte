from __future__ import annotations

from simpy.core import BoundClass
from simpy.resources.base import BaseResource

from simulatte.simpy_extension.multi_store.multi_store_get import MultiStoreGet
from simulatte.simpy_extension.multi_store.multi_store_put import MultiStorePut
from simulatte.environment import Environment
from simulatte.utils import EnvMixin


class MultiStore(BaseResource, EnvMixin):
    """
    The MultiStore works like a 'simpy.resources.store.Store',
    but allows the storage and retrieval of more elements at the same time.

    If the user tries to get more items than the ones available,
    the MultiStore will return all the available items.
    """

    put = BoundClass(MultiStorePut)

    get = BoundClass(MultiStoreGet)

    def __init__(self, *, env: Environment, capacity: float = float("inf")) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0.")

        EnvMixin.__init__(self, env=env)
        BaseResource.__init__(self, self.env, capacity)

        self.items = []

    def _do_put(self, event: MultiStorePut) -> None:
        """
        Put a collection of items into the store, if there is enough space.
        The items to be stored are passed in the 'event.items' attribute.
        """
        if self.level + len(event.items) <= self._capacity:  # enough space
            # store the items
            self.items.extend(event.items)
            # notify the event
            event.succeed()
        return None

    def _do_get(self, event: MultiStoreGet) -> None:
        """
        Get a collection of items from the store.

        If there are fewer items than requested, the MultiStore will return
        all the available items.
        """
        if self.items:  # at least one item is available
            # return the first n items
            to_return, self.items = self.items[: event.n], self.items[event.n :]
            # notify the event
            event.succeed(to_return)

    @property
    def level(self) -> int:
        """
        The number of items currently in the store.
        """
        return len(self.items)
