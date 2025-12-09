from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from simulatte.environment import Environment
from simulatte.typings import ProcessGenerator
from simulatte.utils import EnvMixin, as_process


class MultiStore(EnvMixin):
    """
    Minimal multi-put/get store built on a simple list.

    It keeps FIFO ordering and returns up to ``n`` items on each ``get`` call.
    """

    def __init__(self, *, env: Environment, capacity: float = float("inf")) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0.")

        EnvMixin.__init__(self, env=env)
        self.capacity = capacity
        self.items: list[Any] = []

    @property
    def level(self) -> int:
        return len(self.items)

    @as_process
    def put(self, items: Sequence[Any]) -> ProcessGenerator[None]:
        if len(self.items) + len(items) > self.capacity:
            raise RuntimeError("MultiStore capacity exceeded")

        self.items.extend(items)
        yield self.env.timeout(0)

    @as_process
    def get(self, n: int = 1) -> ProcessGenerator[list[Any]]:
        if n < 1:
            raise ValueError("n must be >= 1")

        take = min(n, len(self.items))
        to_return = self.items[:take]
        del self.items[:take]
        yield self.env.timeout(0)
        return to_return
