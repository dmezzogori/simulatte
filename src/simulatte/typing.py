"""Type aliases for jobshop components."""
# ruff: noqa: UP040 - PEP 695 `type` aliases intentionally backported to TypeAlias for 3.11/PyPy.

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeAlias, TypeVar

from simpy.events import ProcessGenerator

from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

# NOTE: pre-PEP 695 backport (was `type X[T] = ...`) so the package also parses on
# Python 3.11 / PyPy 3.11. Behaviour is identical on CPython 3.12+.
T = TypeVar("T")
K = TypeVar("K")
S = TypeVar("S")

if TYPE_CHECKING:
    # router.py imports this module at runtime, so a runtime import of Router here
    # would be circular; it is referenced below as the string "Router", which keeps
    # the aliases runtime-importable while type-checkers still resolve it.
    from simulatte.router import Router

Distribution: TypeAlias = Callable[[], T]
DiscreteDistribution: TypeAlias = dict[K, T]
Builder: TypeAlias = Callable[..., S]

# "Router" is a forward reference (string) so this evaluates without importing
# router.py at runtime (which would be circular). Subscription below substitutes T.
System: TypeAlias = tuple[T, tuple[Server, ...], ShopFloor, "Router"]
PushSystem: TypeAlias = System[None]
PullSystem: TypeAlias = System[PreShopPool]

__all__ = [
    "Builder",
    "DiscreteDistribution",
    "Distribution",
    "ProcessGenerator",
    "PullSystem",
    "PushSystem",
    "System",
]
