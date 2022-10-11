from __future__ import annotations

from typing import Generic, TypeVar

from simpy.resources.store import Store as SimpyStore

T = TypeVar("T")


class Store(SimpyStore, Generic[T]):
    def put(self, item: T) -> None:
        ...

    def get(self) -> T:
        ...
