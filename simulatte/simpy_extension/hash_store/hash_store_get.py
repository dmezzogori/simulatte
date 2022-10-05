from __future__ import annotations

from typing import TYPE_CHECKING, Hashable

if TYPE_CHECKING:
    from simpy.resources.store import Store

from simpy.resources.store import StoreGet


class HashStoreGet(StoreGet):
    """
    Request to get an *item* from an *HashStore*.
    Must provide the key of the item to get.
    Optionally, a KeyError can be raised if the key is not found within the *HashStore*.
    """

    def __init__(self, resource: Store, key: Hashable, raise_missing=False) -> None:
        self.key = key
        self.raise_missing = raise_missing
        super().__init__(resource)
