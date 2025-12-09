from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from simulatte.requests import OrderLine

if TYPE_CHECKING:
    from collections.abc import Sequence


class ProductRequestSelectionPolicy(Protocol):
    """Callable used to prioritise order lines when needed."""

    def __call__(self, product_requests: Sequence[OrderLine]) -> Sequence[OrderLine]: ...
