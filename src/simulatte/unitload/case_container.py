from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from simulatte.operations.feeding_operation import FeedingOperation
    from simulatte.products import Product
    from simulatte.stores.warehouse_location import WarehouseLocation


class CaseContainer(Protocol):
    product: Product | Sequence[Product] | None = None
    n_cases: int | dict[Product, int]
    warehouse_location: WarehouseLocation | None = None
    location: WarehouseLocation | None = None
    feeding_operation: FeedingOperation | None = None

    def remove_case(self, product: Product | None = None) -> None: ...


class CaseContainerSingleProduct(CaseContainer, Protocol):
    product: Product
    n_cases: int = 0

    @staticmethod
    def by_product(cls, product) -> CaseContainerSingleProduct: ...


class CaseContainerMultiProduct(CaseContainer, Protocol):
    products: Sequence[Product] = []
    n_cases: dict[Product, int]

    @classmethod
    def by_products(cls, *products) -> CaseContainerMultiProduct: ...
