from __future__ import annotations

from collections.abc import Hashable
from typing import Any

from simulatte.environment import Environment
from simulatte.typings import ProcessGenerator
from simulatte.utils import as_process


class HashStore:
    """
    Minimal key/value store with SimPy-friendly put/get.
    """

    def __init__(self, *, env: Environment, capacity: float = float("inf")) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0.")

        self.env = env
        self.capacity = capacity
        self.items: dict[Hashable, Any] = {}

    @property
    def level(self) -> int:
        return len(self.items)

    @as_process
    def put(self, *, key: Hashable, item: Any) -> ProcessGenerator[None]:
        if len(self.items) >= self.capacity:
            raise RuntimeError("HashStore capacity exceeded")
        self.items[key] = item
        yield self.env.timeout(0)

    @as_process
    def get(self, *, key: Hashable, raise_missing: bool = False) -> ProcessGenerator[Any]:
        yield self.env.timeout(0)
        try:
            return self.items.pop(key)
        except KeyError as exc:
            if raise_missing:
                raise exc
            return None
