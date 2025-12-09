"""Type aliases for jobshop components."""

from __future__ import annotations

from collections.abc import Callable

from simpy.events import ProcessGenerator

from simulatte.jobshop.psp.psp import PreShopPool
from simulatte.jobshop.router import Router
from simulatte.jobshop.server.server import Server
from simulatte.jobshop.shopfloor import ShopFloor

type System[T] = tuple[T, tuple[Server, ...], ShopFloor, Router]
type PushSystem = System[None]
type PullSystem = System[PreShopPool]

type Builder[S] = Callable[..., S]

__all__ = [
    "Builder",
    "ProcessGenerator",
    "PullSystem",
    "PushSystem",
    "System",
]
