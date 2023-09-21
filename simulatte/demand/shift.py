from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from simulatte.demand.customer_order import CustomerOrder
from simulatte.requests import LayerRequest, PalletRequest


@dataclass
class Shift:
    """
    Represent a shift, during which a given set of customer orders must be satisfied.
    """

    day: int
    shift: int
    customer_orders: list[CustomerOrder]

    @property
    def pallet_requests(self) -> Iterable[PalletRequest]:
        for customer_order in self.customer_orders:
            yield from customer_order.pallet_requests

    @property
    def layer_requests(self) -> Iterable[LayerRequest]:
        for pallet_request in self.pallet_requests:
            yield from pallet_request.sub_requests

    @property
    def perc_mono_sku_layers(self) -> float:
        return statistics.mean(layer.has_single_product_request for layer in self.layer_requests)
