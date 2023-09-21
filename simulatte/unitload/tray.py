from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.unitload.case_container import CaseContainer

if TYPE_CHECKING:
    from simulatte.products import Product


class Tray(CaseContainer):
    def __init__(self, *, product: Product, n_cases: int, exceed=False) -> None:
        if n_cases > product.cases_per_layer and not exceed:
            raise ValueError(
                f"A Tray cannot hold n_cases={n_cases} [product.cases_per_layer={product.cases_per_layer}]"
            )
        self.product = product
        self.n_cases = n_cases

    def __repr__(self) -> str:
        return f"Tray(product={self.product}, n_cases={self.n_cases})"
