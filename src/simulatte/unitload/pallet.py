from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Protocol

from simulatte.unitload.case_container import CaseContainer
from simulatte.unitload.layer import LayerMultiProduct, LayerSingleProduct

if TYPE_CHECKING:
    from simulatte.products import Product


class Pallet(CaseContainer, Protocol):
    layers: deque[CaseContainer]

    def __str__(self):
        return f"{self.__class__.__name__}[{self.product}]"

    @property
    def upper_layer(self) -> CaseContainer | None:
        if self.layers:
            return self.layers[-1]

    def remove_layer(self) -> None:
        """
        Removes the most accessible layer from the unit load.
        """
        self.layers.pop()


class PalletSingleProduct(Pallet):
    layers: deque[LayerSingleProduct]
    product: Product
    n_cases: int

    def __init__(self, *layers: LayerSingleProduct) -> None:
        self.layers = deque(layers)
        self.counter = 0
        self.counter_pr = []
        if self.layers:
            self.product = self.layers[0].product
            self.n_cases = sum(int(layer.n_cases) for layer in self.layers)

    @property
    def upper_layer(self) -> LayerSingleProduct | None:
        if self.layers:
            return self.layers[-1]

    @staticmethod
    def by_product(product: Product) -> PalletSingleProduct:
        return PalletSingleProduct(
            *(
                LayerSingleProduct(product=product, n_cases=product.cases_per_layer)
                for _ in range(product.layers_per_pallet)
            ),
        )


class PalletMultiProduct(Pallet):
    n_cases: dict[Product, int]

    def __init__(self, *layers: LayerMultiProduct | LayerSingleProduct) -> None:
        self.layers: deque[LayerMultiProduct | LayerSingleProduct] = deque(layers)
        products_set: set[Product] = set()
        counts: dict[Product, int] = {}

        for layer in self.layers:
            if isinstance(layer, LayerSingleProduct):
                products_set.add(layer.product)
                counts[layer.product] = counts.get(layer.product, 0) + int(layer.n_cases)
            else:
                products_set.update(layer.products)
                for product, n_cases in layer.n_cases.items():
                    counts[product] = counts.get(product, 0) + n_cases

        self.products = tuple(products_set)
        self.n_cases = counts

    @property
    def upper_layer(self) -> LayerSingleProduct | LayerMultiProduct | None:
        if self.layers:
            return self.layers[-1]

    def add_product(self, *, product: Product, n_cases: int) -> None:
        single_product_layer = product.cases_per_layer == n_cases

        # If the number of cases is equal to the number of cases per layer of the product,
        # we can add a single product layer
        if single_product_layer:
            self.layers.append(LayerSingleProduct(product=product, n_cases=n_cases))
        else:
            if self.upper_layer is None or isinstance(self.upper_layer, LayerSingleProduct):
                # If the upper layer is None or a single product layer, we create a new multi product layer
                layer = LayerMultiProduct()
                layer.add_product(product=product, n_cases=n_cases)
                self.layers.append(layer)
            else:
                # If the upper layer is a multi product layer, we add the product to the layer
                self.upper_layer.add_product(product=product, n_cases=n_cases)

        self.products = (*self.products, product)
        if product in self.n_cases:
            self.n_cases[product] += n_cases
        else:
            self.n_cases[product] = n_cases
