"""Base classes for pre-shop pool (PSP) release policies."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.psp import PreShopPool
    from simulatte.shopfloor import ShopFloor


class PSPReleasePolicy:
    """Base class for PSP release policies."""

    def release_condition(self, psp: PreShopPool, shopfloor: ShopFloor) -> bool:
        raise NotImplementedError

    def release(self, psp: PreShopPool, shopfloor: ShopFloor) -> None:
        if self.release_condition(psp, shopfloor):
            job = psp.remove()
            shopfloor.add(job)
