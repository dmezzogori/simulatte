from __future__ import annotations

from typing import TYPE_CHECKING, Hashable, Any

if TYPE_CHECKING:
    from simpy.resources.store import Store

from simpy.resources.store import StorePut


class HashStorePut(StorePut):
    """
    Request to put an *item* into an *HashStore*.
    Must provide the key mapping the item to put.
    """

    def __init__(self, resource: Store, key: Hashable, item: Any) -> None:
        self.key = key
        super().__init__(resource, item)
