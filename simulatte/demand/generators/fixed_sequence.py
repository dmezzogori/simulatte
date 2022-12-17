from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from simulatte.products import Product

from simulatte.requests import LayerRequest, PalletRequest, ProductRequest


def fixed_sequence(products: list[Product], n_pallet_requests: int, n_layers: int) -> Iterable[PalletRequest]:
    i = -1
    sequence = [[i := i + 1 for _ in range(n_layers)] for _ in range(n_pallet_requests)]

    for r in sequence:
        yield PalletRequest(*[LayerRequest(ProductRequest(products[i], products[i].cases_per_layer)) for i in r])
