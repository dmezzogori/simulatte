from __future__ import annotations

from collections.abc import Callable
from typing import Any

from simulatte.environment import Environment
from simulatte.typings import ProcessGenerator
from simulatte.utils import as_process
from simulatte.simpy_extension.multi_store import MultiStore


class FilterMultiStore(MultiStore):
    """
    Variant of MultiStore that can return items matching a predicate.
    If no filter is provided, all items are returned.
    """

    def __init__(self, *, env: Environment, capacity: float = float("inf")) -> None:
        super().__init__(env=env, capacity=capacity)

    @as_process
    def get(self, filter: Callable[[Any], bool] | None = None) -> ProcessGenerator[list[Any]]:
        if filter is None:
            selected = list(self.items)
            self.items.clear()
            yield self.env.timeout(0)
            return selected

        selected: list[Any] = []
        remaining: list[Any] = []
        for item in self.items:
            (selected if filter(item) else remaining).append(item)

        self.items = remaining
        yield self.env.timeout(0)
        return selected
