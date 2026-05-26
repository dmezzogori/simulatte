"""Type aliases for jobshop components."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from simpy.events import ProcessGenerator

from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:
    from simulatte.job import BaseJob
    from simulatte.router import Router

type Distribution[T] = Callable[[], T]
type DiscreteDistribution[K, T] = dict[K, T]

type System[T] = tuple[T, tuple[Server, ...], ShopFloor, Router]
type PushSystem = System[None]
type PullSystem = System[PreShopPool]

type Builder[S] = Callable[..., S]

type DispatchingRule = Callable[[BaseJob, Server], float]
"""A queue-ordering callable: ``(job, server) -> float``. Lower value =
served first. See :mod:`simulatte.dispatching_rules` for built-in rules
and the conventions for adding new ones."""

__all__ = [
    "Builder",
    "DiscreteDistribution",
    "DispatchingRule",
    "Distribution",
    "ProcessGenerator",
    "PullSystem",
    "PushSystem",
    "System",
]
