from __future__ import annotations

from collections.abc import Callable
from typing import Any

from simulatte.environment import Environment
from simulatte.typings import ProcessGenerator
from simulatte.utils import EnvMixin, as_process


class SequentialStore(EnvMixin):
    """
    Thin FIFO queue. Optional filter on ``get`` picks the first matching item.
    """

    def __init__(self, *, env: Environment, capacity: float = float("inf")) -> None:
        if capacity < 1:
            raise ValueError("Capacity of SequentialStore must be at least 1.")

        EnvMixin.__init__(self, env=env)
        self.capacity = capacity
        self.items: list[Any] = []

    @property
    def internal_store_level(self) -> int:
        return 0

    @property
    def output_level(self) -> int:
        return len(self.items)

    @property
    def level(self) -> int:
        return len(self.items)

    @as_process
    def put(self, item: Any) -> ProcessGenerator[None]:
        if len(self.items) >= self.capacity:
            raise RuntimeError("SequentialStore capacity exceeded")
        self.items.append(item)
        yield self.env.timeout(0)

    @as_process
    def get(self, filter_: Callable[[Any], bool] | None = None) -> ProcessGenerator[Any]:
        if not self.items:
            yield self.env.timeout(0)
            return None

        if filter_ is None:
            item = self.items.pop(0)
        else:
            for idx, candidate in enumerate(self.items):
                if filter_(candidate):
                    item = self.items.pop(idx)
                    break
            else:
                item = None
        yield self.env.timeout(0)
        return item
