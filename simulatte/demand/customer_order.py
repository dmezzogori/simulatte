from __future__ import annotations

from dataclasses import dataclass

from simulatte.requests import PalletRequest


@dataclass
class CustomerOrder:
    """
    Represent a client order.
    """

    day: int
    shift: int
    pallet_requests: list[PalletRequest]
