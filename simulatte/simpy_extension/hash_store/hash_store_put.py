from __future__ import annotations

from typing import TYPE_CHECKING, Any

from simpy.resources.store import StorePut

if TYPE_CHECKING:
    from collections.abc import Hashable

    from simpy.resources.store import Store


class HashStorePut(StorePut):
    """
    Request to put an *item* into an *HashStore*.
    Must provide the key mapping the item to put.
    """

    def __init__(self, resource: Store, key: Hashable, item: Any) -> None:
        self.key = key
        super().__init__(resource, item)
