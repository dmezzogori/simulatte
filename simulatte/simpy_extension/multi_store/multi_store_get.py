from __future__ import annotations

from typing import TYPE_CHECKING

from simpy.resources.base import Get

if TYPE_CHECKING:
    from .multi_store import MultiStore


class MultiStoreGet(Get):
    """Request to get *n* items from a *MultiStore*."""

    def __init__(self, store: MultiStore, n: int = 1) -> None:
        self.n = n
        super().__init__(store)
