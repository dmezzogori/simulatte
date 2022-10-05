from __future__ import annotations

from simpy.core import BoundClass

from .filter_multi_store_get import FilterMultiStoreGet
from ..multi_store import MultiStore


class FilterMultiStore(MultiStore):
    """
    The FilterMultiStore is an extension to the MultiStore which allows the
    storage and retrieval of multiple items at once based on a 'filter' callable.
    """

    get = BoundClass(FilterMultiStoreGet)

    def _do_get(self, event: FilterMultiStoreGet) -> None:
        to_retrieve = []
        for item in self.items:
            if event.filter(item):
                self.items.remove(item)
                to_retrieve.append(item)

        if len(to_retrieve) > 0:
            event.succeed(to_retrieve)
