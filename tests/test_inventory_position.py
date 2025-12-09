from __future__ import annotations

from typing import cast

from simulatte.products import Product
from simulatte.stores.inventory_position import OnHand, OnOrder


class FakeProduct:
    def __init__(self, *, cases_per_layer: int, layers_per_pallet: int) -> None:
        self.cases_per_layer = cases_per_layer
        self.layers_per_pallet = layers_per_pallet


def test_on_hand_layers_and_pallets_computed():
    product = FakeProduct(cases_per_layer=5, layers_per_pallet=2)
    stock = OnHand(product=cast(Product, product), n_cases=20)

    assert stock.n_layers == 4
    assert stock.n_pallets == 2


def test_on_order_inherits_stock_behavior():
    product = FakeProduct(cases_per_layer=4, layers_per_pallet=1)
    stock = OnOrder(product=cast(Product, product), n_cases=8)

    assert stock.product is product
    assert stock.n_layers == 2
    assert stock.n_pallets == 2
