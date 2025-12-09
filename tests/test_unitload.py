from __future__ import annotations

import pytest

from simulatte.products import Product
from simulatte.unitload.layer import LayerMultiProduct, LayerSingleProduct
from simulatte.unitload.pallet import PalletMultiProduct, PalletSingleProduct


def make_product(cases_per_layer=4, layers_per_pallet=2) -> Product:
    return Product(
        probability=0.5,
        family="X",
        cases_per_layer=cases_per_layer,
        layers_per_pallet=layers_per_pallet,
        max_case_per_pallet=cases_per_layer * layers_per_pallet,
        min_case_per_pallet=cases_per_layer,
        lp_enabled=True,
    )


def test_layer_single_product_validations_and_remove_case():
    product = make_product()
    layer = LayerSingleProduct(product=product, n_cases=3)
    assert layer.n_cases == 3
    layer.remove_case()
    assert layer.n_cases == 2

    with pytest.raises(ValueError):
        LayerSingleProduct(product=product, n_cases=product.cases_per_layer + 1)


def test_layer_multi_product_add_product_and_limits():
    product = make_product()
    layer = LayerMultiProduct()
    layer.add_product(product=product, n_cases=product.cases_per_layer)
    assert layer.products == [product]
    assert layer.n_cases[product] == product.cases_per_layer

    with pytest.raises(ValueError):
        layer.add_product(product=product, n_cases=product.cases_per_layer + 1)


def test_pallet_single_product_by_product_and_upper_layer():
    product = make_product()
    pallet = PalletSingleProduct.by_product(product)
    assert pallet.product is product
    assert pallet.upper_layer.product is product
    assert pallet.n_cases == product.case_per_pallet
    assert len(pallet.layers) == product.layers_per_pallet


def test_pallet_multi_product_adds_layers_and_case_counts():
    p1 = make_product()
    p2 = make_product(cases_per_layer=2)
    pallet = PalletMultiProduct()

    pallet.add_product(product=p1, n_cases=p1.cases_per_layer)  # single-product layer
    assert pallet.upper_layer.product is p1
    assert pallet.n_cases[p1] == p1.cases_per_layer

    # Add partial layer for p2, should create multi-product layer
    pallet.add_product(product=p2, n_cases=1)
    assert p2 in pallet.products
    assert pallet.n_cases[p2] == 1
    assert len(pallet.layers) == 2

    # Adding again to existing multi layer aggregates counts
    pallet.add_product(product=p2, n_cases=1)
    assert pallet.n_cases[p2] == 2
