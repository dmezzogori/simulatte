from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..products import Product
from .identifiable import Identifiable


class HasSingleProduct(Identifiable, Protocol):
    product: Product


class HasManyProduct(Identifiable, Protocol):
    products: Sequence[Product]
