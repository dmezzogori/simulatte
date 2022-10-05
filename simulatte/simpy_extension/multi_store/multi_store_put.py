from __future__ import annotations

from typing import TYPE_CHECKING, Sequence, Any

if TYPE_CHECKING:
    from .multi_store import MultiStore

from simpy.resources.base import Put


class MultiStorePut(Put):
    """Request to put *items* into a *MultiStore*."""

    def __init__(self, store: MultiStore, items: Sequence[Any]) -> None:
        self.items = items
        super().__init__(store)
