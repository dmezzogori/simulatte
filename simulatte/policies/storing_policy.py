from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.products import Product
    from simulatte.stores import WarehouseStore
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class StoringPolicy(Callable):
    """
    StoringPolicy defines the interface for implementing policies that determine
    where to store a product in a warehouse.

    The __call__ method takes the following parameters:

    store: WarehouseStore - The warehouse store to find a location in.

    product: Product - The product that needs to be stored.

    It returns either a WarehouseLocation object representing the location
    to store the product, or None if no suitable location was found.

    Subclasses should implement the logic to choose the best location for
    storing the given product in the given warehouse store.
    """

    def __call__(self, *, store: WarehouseStore, product: Product) -> WarehouseLocation | None:
        pass
