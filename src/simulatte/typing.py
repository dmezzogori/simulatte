"""Type aliases for jobshop components."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from simpy.events import ProcessGenerator

from simulatte.agv import AGV
from simulatte.materials import MaterialCoordinator
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.warehouse import Warehouse

if TYPE_CHECKING:
    from simulatte.router import Router

type Distribution[T] = Callable[[], T]
type DiscreteDistribution[K, T] = dict[K, T]

type System[T] = tuple[T, tuple[Server, ...], ShopFloor, Router]
type PushSystem = System[None]
type PullSystem = System[PreShopPool]

type MaterialSystem = tuple[
    ShopFloor,
    tuple[Server, ...],
    Warehouse,
    tuple[AGV, ...],
    MaterialCoordinator,
]

type Builder[S] = Callable[..., S]

__all__ = [
    "Builder",
    "DiscreteDistribution",
    "Distribution",
    "MaterialSystem",
    "ProcessGenerator",
    "PullSystem",
    "PushSystem",
    "System",
]
