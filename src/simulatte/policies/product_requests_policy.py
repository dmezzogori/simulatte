from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from simulatte.protocols.request import ProductRequest

if TYPE_CHECKING:
    from collections.abc import Sequence


class ProductRequestSelectionPolicy(Protocol):
    def __call__(self, product_requests: Sequence[ProductRequest]) -> Sequence[ProductRequest]: ...
