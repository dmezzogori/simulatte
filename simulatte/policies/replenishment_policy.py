from __future__ import annotations

from typing import Protocol

from simulatte.products import Product
from simulatte.unitload import CaseContainer


class ReplenishmentPolicy(Protocol):
    """
    Base class for implementing replenishment policies.

    Replenishment policies are responsible for triggering replenishment requests when needed.

    """

    def __call__(self, *, product: Product, case_container: type[CaseContainer], **kwargs) -> None:
        ...
