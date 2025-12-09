from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from simpy.resources.base import Put

if TYPE_CHECKING:
    from .multi_store import MultiStore


class MultiStorePut(Put):
    """Request to put *items* into a *MultiStore*."""

    def __init__(self, store: MultiStore, items: Sequence[Any]) -> None:
        self.items = items
        super().__init__(store)
