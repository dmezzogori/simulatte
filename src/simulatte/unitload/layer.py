from __future__ import annotations

from simulatte.products import Product
from simulatte.unitload.case_container import (
    CaseContainerMultiProduct,
    CaseContainerSingleProduct,
)
from simulatte.utils import IdentifiableMixin


class LayerSingleProduct(IdentifiableMixin, CaseContainerSingleProduct):
    def __init__(self, *, product: Product, n_cases: int) -> None:
        IdentifiableMixin.__init__(self)

        if n_cases > product.cases_per_layer:
            raise ValueError(
                f"A Layer cannot hold n_cases={n_cases} [product.cases_per_layer={product.cases_per_layer}]"
            )

        self.product = product
        self.n_cases = n_cases

    def remove_case(self, product: Product | None = None) -> None:
        self.n_cases -= 1


class LayerMultiProduct(IdentifiableMixin, CaseContainerMultiProduct):
    def __init__(self) -> None:
        IdentifiableMixin.__init__(self)

        self.products: list[Product] = []
        self.n_cases: dict[Product, int] = {}

    def add_product(self, *, product: Product, n_cases: int) -> None:
        if n_cases > product.cases_per_layer:
            raise ValueError(
                f"A Layer cannot hold n_cases={n_cases} [product.cases_per_layer={product.cases_per_layer}]"
            )

        self.products.append(product)
        self.n_cases[product] = n_cases
