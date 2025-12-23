"""Type aliases for jobshop components."""

from __future__ import annotations

from collections.abc import Callable

from simpy.events import ProcessGenerator

from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

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
