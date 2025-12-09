from __future__ import annotations

from collections.abc import Hashable
from typing import Any

from simpy.core import BoundClass
from simpy.resources.store import Store, StoreGet, StorePut

from simulatte.simpy_extension.hash_store.hash_store_get import HashStoreGet
from simulatte.simpy_extension.hash_store.hash_store_put import HashStorePut


class HashStore(Store):
    """
    Like a FilterStore but the research is more efficient
    because based on a HashMap.

    The HashStore works like a 'simpy.resource.store.FilterStore',
    but allows the storage and retrieval of an item based on efficient mapping.
    """

    get = BoundClass(HashStoreGet)  # type: ignore
    put = BoundClass(HashStorePut)  # type: ignore

    def __init__(self, env, capacity=float("inf")) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0.")

        super().__init__(env, capacity)
        self.items: dict[Hashable, Any] = {}  # type: ignore

    def _do_put(self, event: StorePut) -> bool | None:
        """
        Put an item into the store, if there is enough space.
        The storage of the item is based on the key passed in the 'event.key' attribute.
        """
        if self.level < self._capacity:
            key = getattr(event, "key", None)
            if key is None:
                return None
            self.items[key] = event.item
            event.succeed()
        return None

    def _do_get(self, event: StoreGet) -> bool | None:
        """
        Get an item from the store.
        The retrieval of the item is based on the key passed in the 'event.key' attribute.
        If the key is not found, the event is not triggered.
        Optionally, a KeyError can be raised if the key is not found.
        """
        try:
            item = self.items.pop(getattr(event, "key"))
            event.succeed(item)
        except KeyError as e:
            if getattr(event, "raise_missing", False):
                raise e
            else:
                return None
        return None

    @property
    def level(self) -> int:
        """
        The number of items currently in the store.
        """
        return len(self.items)
