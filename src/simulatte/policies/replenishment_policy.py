from __future__ import annotations

from typing import Protocol

from simulatte.products import Product
from simulatte.protocols import WarehouseStoreProtocol


class ReplenishmentPolicy(Protocol):
    """
    Base class for implementing replenishment policies.

    Replenishment policies are responsible for triggering replenishment requests when needed.

    """

    def __call__(self, *, store_type: type[WarehouseStoreProtocol], product: Product, **kwargs) -> None:
        ...

    def periodic_store_replenishment(self):
        ...
