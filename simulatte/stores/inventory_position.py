from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulatte.products import Product


class Stock:
    def __init__(self, *, product: Product, n_cases: int) -> None:
        self.product = product
        self.n_cases = n_cases

    @property
    def n_layers(self) -> float:
        return self.n_cases / self.product.cases_per_layer

    @property
    def n_pallets(self) -> float:
        return self.n_layers / self.product.layers_per_pallet


class OnHand(Stock):
    pass


class OnOrder(Stock):
    pass
