from __future__ import annotations

import random

from simulatte.products import Product, ProductsGenerator


def make_config(n: int = 3):
    return {
        "n_products": n,
        "probabilities": None,
        "families": None,
        "cases_per_layers": lambda: 5,
        "layers_per_pallet": lambda: 2,
        "min_case_per_pallet": lambda: 4,
        "max_case_per_pallet": lambda: 10,
        "lp_enable": lambda: True,
    }


def test_product_case_per_pallet():
    product = Product(
        probability=0.5,
        family="A",
        cases_per_layer=6,
        layers_per_pallet=3,
        max_case_per_pallet=30,
        min_case_per_pallet=18,
        lp_enabled=True,
    )

    assert product.case_per_pallet == 18
    assert repr(product) == f"Product(id={product.id})"


def test_products_generator_defaults_and_lazy_creation():
    generator = ProductsGenerator(config=make_config())

    # Products are lazily created
    assert generator._products is None
    products = generator.products
    assert len(products) == 3
    assert generator._products is products

    # Defaults are applied
    assert all(p.family == "A" for p in products)
    assert all(p.lp_enabled for p in products)
    assert all(p.cases_per_layer == 5 for p in products)
    assert all(p.layers_per_pallet == 2 for p in products)


def test_choose_one_respects_exclude_and_custom_function():
    random.seed(0)
    generator = ProductsGenerator(config=make_config(n=2))
    products = generator.products

    exclude = {products[0]}
    chosen = generator.choose_one(exclude=exclude)
    assert chosen is products[1]
    assert products[1] in exclude  # exclude set updated

    # Custom selector is used verbatim
    def pick_last(items):
        return items[-1]

    picked = generator.choose_one(fn=pick_last)
    assert picked is products[-1]
