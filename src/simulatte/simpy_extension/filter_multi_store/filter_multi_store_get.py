from __future__ import annotations

from typing import TYPE_CHECKING

from simpy.resources.base import Get

if TYPE_CHECKING:
    from collections.abc import Callable

    from .filter_multi_store import FilterMultiStore


class FilterMultiStoreGet(Get):
    """
    Request to get items from a FilterMultiStore based on a 'filter' callable.
    """

    def __init__(self, store: FilterMultiStore, filter: Callable):
        self.filter = filter
        super().__init__(store)
