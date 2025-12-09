from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from simulatte.protocols.warehouse_store import WarehouseStoreProtocol
from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation
from simulatte.unitload import CaseContainer

if TYPE_CHECKING:
    from simulatte.products import Product


CasesQuantity = int
RetrievalPolicyResult = tuple[tuple[WarehouseStoreProtocol, WarehouseLocation, CaseContainer], ...]


class RetrievalPolicy(Protocol):
    """
    RetrievalPolicy defines the interface for implementing policies for determine
    which unit load to retrieve, and from which store to retrieve it, in order to satisfy a request.

    The __call__ method takes the following parameters:

    stores: list[WarehouseStore] - A list of WarehouseStore objects representing the stores to consider.

    product: Product - The Product object representing the product to retrieve.

    quantity: CasesQuantity - The quantity (number of cases) of the product to retrieve.

    It returns a RetrievalPolicyResult, which is a tuple of tuples, where each inner tuple contains:
     - A WarehouseStore object
     - A WarehouseLocation object
     - A Pallet object

    The inner tuples represent the specific store, location, and pallet to retrieve
    the requested quantity of the product from.

    Subclasses should implement the logic to determine the optimal combination of
    stores, locations, and pallets to retrieve the requested quantity of the product.
    """

    def __call__(
        self,
        *,
        stores: list[WarehouseStoreProtocol],
        product: Product,
        quantity: CasesQuantity,
    ) -> RetrievalPolicyResult: ...
