from __future__ import annotations

import random
from collections.abc import Callable
from typing import TypedDict, TypeVar

from simulatte.utils import IdentifiableMixin

T = TypeVar("T")
DistributionCallable = Callable[[], T]


class Product(IdentifiableMixin):
    def __init__(
        self,
        *,
        probability: float,
        family: str,
        cases_per_layer: int,
        layers_per_pallet: int,
        max_case_per_pallet: int,
        min_case_per_pallet: int,
        lp_enabled: bool,
    ) -> None:
        super().__init__()
        self.probability = probability
        self.family = family
        self.cases_per_layer = cases_per_layer
        self.layers_per_pallet = layers_per_pallet
        self.max_case_per_pallet = max_case_per_pallet
        self.min_case_per_pallet = min_case_per_pallet
        self.lp_enabled = lp_enabled
        self.s_max = {
            "pallet": 0,  # in cases
            "tray": 0,  # in cases
        }
        self.s_min = {
            "pallet": 0,  # in cases
            "tray": 0,  # in cases
        }

    def __repr__(self) -> str:
        return f"Product(id={self.id})"

    @property
    def case_per_pallet(self) -> int:
        return self.cases_per_layer * self.layers_per_pallet


class ProductsGeneratorConfig(TypedDict):
    n_products: int
    probabilities: DistributionCallable[list[float]] | None
    families: DistributionCallable[list[str]] | None
    cases_per_layers: DistributionCallable[int] | None
    layers_per_pallet: DistributionCallable[int] | None
    min_case_per_pallet: DistributionCallable[int] | None
    max_case_per_pallet: DistributionCallable[int] | None
    lp_enable: DistributionCallable[bool] | None


class ProductsGenerator:
    def __init__(self, *, config: ProductsGeneratorConfig) -> None:
        n_products = config["n_products"]
        probabilities = config["probabilities"]
        families = config["families"]
        cases_per_layers = config["cases_per_layers"]
        layers_per_pallet = config["layers_per_pallet"]
        min_case_per_pallet = config["min_case_per_pallet"]
        max_case_per_pallet = config["max_case_per_pallet"]
        lp_enable = config["lp_enable"]

        self.probabilities: list[float] = (
            probabilities() if probabilities is not None else [1 / n_products for _ in range(n_products)]
        )
        self.families: list[str] = families() if families is not None else ["A"] * n_products
        self.cases_per_layers: DistributionCallable[int] = cases_per_layers or (lambda: 10)
        self.layers_per_pallet: DistributionCallable[int] = layers_per_pallet or (lambda: 4)
        self.min_case_per_pallet: DistributionCallable[int] = min_case_per_pallet or (lambda: 60)
        self.max_case_per_pallet: DistributionCallable[int] = max_case_per_pallet or (lambda: 60)
        self.lp_enable: DistributionCallable[bool] = lp_enable or (lambda: True)

        self._products: list[Product] | None = None

    @property
    def products(self) -> list[Product]:
        if self._products is None:
            self._products = [
                Product(
                    probability=probability,
                    family=family,
                    cases_per_layer=self.cases_per_layers(),
                    layers_per_pallet=self.layers_per_pallet(),
                    max_case_per_pallet=self.max_case_per_pallet(),
                    min_case_per_pallet=self.min_case_per_pallet(),
                    lp_enabled=self.lp_enable(),
                )
                for probability, family in zip(self.probabilities, self.families)
            ]
        return self._products

    def choose_one(
        self,
        *,
        exclude: set[Product] | None = None,
        fn: Callable[[list[Product]], Product] | None = None,
    ) -> Product:
        def choose():
            if fn is not None:
                product = fn(self.products)
            else:
                product: Product = random.choices(self.products, weights=self.probabilities, k=1)[0]

            return product

        product = choose()

        if exclude is not None:
            while product in exclude:
                product = choose()

            exclude.add(product)

        return product
