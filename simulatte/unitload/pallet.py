from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from .case_container import CaseContainer
from .tray import Tray
from .wood_board import WoodBoard

if TYPE_CHECKING:
    from simulatte.products import Product


class Pallet(CaseContainer):
    id_counter = 0
    def __init__(self, *layers: Tray, wood_board=False) -> None:
        if wood_board:
            self.layers = deque((WoodBoard(), *layers))
        else:
            self.layers = deque(layers)
        self.location = None
        self.feeding_operation = None
        self.id_ = Pallet.id_counter
        Pallet.id_counter += 1

    @classmethod
    def by_product(cls, *, product: Product) -> Pallet:
        return cls(
            *(Tray(product=product, n_cases=product.cases_per_layer) for _ in range(product.layers_per_pallet)),
        )

    def __repr__(self) -> str:
        return f"Pallet(*{self.layers})"

    @classmethod
    def single_tray(cls, *, product: Product, n_cases: int | None = None) -> Pallet:
        return Pallet(*[Tray(product=product, n_cases=n_cases if n_cases is not None else product.cases_per_layer)])

    @property
    def upper_layer(self) -> Tray | None:
        if self.n_layers > 0:
            return self.layers[-1]

    @property
    def product(self) -> Product | None:
        """The product accessible on the unit load --i.e., the product on the last layer."""
        if self.upper_layer is not None:
            return self.upper_layer.product

    @property
    def n_cases(self) -> int:
        """The number of cases on the unit load"""
        trays = (layer for layer in self.layers if isinstance(layer, Tray))
        return sum(tray.n_cases for tray in trays)

    @property
    def n_layers(self) -> int:
        """The number of Trays on the unit load"""
        return sum(isinstance(layer, Tray) for layer in self.layers)

    def remove_case(self) -> None:
        """
        Removes a single case from the most accessible layer
        """
        self.upper_layer.n_cases -= 1

    def remove_layer(self) -> None:
        """
        Removes the most accessible layer from the unit load.
        """
        self.layers.pop()

    def add_layer(self, *, product: Product, n_cases: int) -> None:
        """
        Adds a layer to the unit load.
        """
        self.layers.append(Tray(product=product, n_cases=n_cases))
