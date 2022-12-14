from __future__ import annotations

import random
from typing import Callable, Optional, TypeVar

import numpy as np

from simulatte.utils import Identifiable

T = TypeVar("T")
DistributionCallable = Callable[[], T]


class Product(metaclass=Identifiable):
    id: int

    def __init__(
        self,
        *,
        probability: float,
        cases_per_layer: int,
        layers_per_pallet: int,
        max_case_per_pallet: int,
        min_case_per_pallet: int,
        lp_enabled: bool,
        reorder_level: int,
    ) -> None:
        self.probability = probability
        self.cases_per_layer = cases_per_layer
        self.layers_per_pallet = layers_per_pallet
        self.max_case_per_pallet = max_case_per_pallet
        self.min_case_per_pallet = min_case_per_pallet
        self.lp_enabled = lp_enabled
        self.reorder_level = reorder_level

    def __repr__(self) -> str:
        return f"Product(id={self.id})"


class ProductsGenerator:
    def __init__(
        self,
        *,
        n_products: int,
        probabilities: Optional[DistributionCallable[list[float]]] = None,
        cases_per_layers: Optional[DistributionCallable[int]] = None,
        layers_per_pallet: Optional[DistributionCallable[int]] = None,
        min_case_per_pallet: Optional[DistributionCallable[int]] = None,
        max_case_per_pallet: Optional[DistributionCallable[int]] = None,
        lp_enable: Optional[DistributionCallable[bool]] = None,
        reorder_level: Optional[DistributionCallable[int]] = None,
    ) -> None:

        self.probabilities: list[float] = (
            probabilities() if probabilities is not None else [1 / n_products for _ in range(n_products)]
        )
        self.cases_per_layers: DistributionCallable[int] = cases_per_layers or (lambda: 10)
        self.layers_per_pallet: DistributionCallable[int] = layers_per_pallet or (lambda: 4)
        self.min_case_per_pallet: DistributionCallable[int] = min_case_per_pallet or (lambda: 60)
        self.max_case_per_pallet: DistributionCallable[int] = max_case_per_pallet or (lambda: 60)
        self.lp_enable: DistributionCallable[bool] = lp_enable or (lambda: True)
        self.reorder_level: DistributionCallable[int] = reorder_level or (lambda: 4)

        self._products: list[Product] | None = None

    @property
    def products(self) -> list[Product]:
        if self._products is None:
            self._products = [
                Product(
                    probability=probability,
                    cases_per_layer=self.cases_per_layers(),
                    layers_per_pallet=self.layers_per_pallet(),
                    max_case_per_pallet=self.max_case_per_pallet(),
                    min_case_per_pallet=self.min_case_per_pallet(),
                    lp_enabled=self.lp_enable(),
                    reorder_level=self.reorder_level(),
                )
                for probability in self.probabilities
            ]
        return self._products

    def choose_one(
        self, *, exclude: set[Product] | None = None, fn: Optional[Callable[[list[Product]], Product]] = None
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

    def choose_some(
        self, *, n: int = 1, replace=False, fn: Optional[Callable[[list[Product]], Product]] = None
    ) -> list[Product]:
        if n > len(self.products):
            raise ValueError("Cannot generate a sample larger than the population")

        if fn is not None:
            products = list(fn(self.products) for _ in range(n))
        else:
            products: list[Product] = np.random.choice(self.products, n, replace=replace, p=self.probabilities)

        return products
