from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .filter_multi_store import FilterMultiStore

from simpy.resources.base import Get


class FilterMultiStoreGet(Get):
    """
    Request to get items from a FilterMultiStore based on a 'filter' callable.
    """

    def __init__(self, store: FilterMultiStore, filter: Callable):
        self.filter = filter
        super().__init__(store)
