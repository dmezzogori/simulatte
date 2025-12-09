from __future__ import annotations

from simpy.core import BoundClass
from simulatte.simpy_extension.filter_multi_store.filter_multi_store_get import FilterMultiStoreGet
from simulatte.simpy_extension.multi_store.multi_store import MultiStore
from simulatte.simpy_extension.multi_store.multi_store_get import MultiStoreGet


class FilterMultiStore(MultiStore):
    """
    The FilterMultiStore is an extension to the MultiStore which allows the
    storage and retrieval of multiple items at once based on a 'filter' callable.
    """

    get = BoundClass(FilterMultiStoreGet)

    def _do_get(self, event: MultiStoreGet) -> None:
        to_retrieve = []
        filter_fn = getattr(event, "filter", None)
        for item in list(self.items):
            if filter_fn is None or filter_fn(item):
                self.items.remove(item)
                to_retrieve.append(item)

        if len(to_retrieve) > 0:
            event.succeed(to_retrieve)
