"""Type aliases and shared structured types for jobshop components."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Generic, NamedTuple, TypeAlias, TypeVar

from simpy.events import ProcessGenerator

from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

# NOTE: pre-PEP 695 backport (was `type X[T] = ...`) so the package also parses on
# Python 3.11 / PyPy 3.11. Behaviour is identical on CPython 3.12+.
T = TypeVar("T")
K = TypeVar("K")
S = TypeVar("S")
PolicyT = TypeVar("PolicyT")

if TYPE_CHECKING:
    # router.py imports this module at runtime, so a runtime import of Router here
    # would be circular; it is referenced below as the string "Router", which keeps
    # the aliases runtime-importable while type-checkers still resolve it.
    from simulatte.router import Router

Sampler: TypeAlias = Callable[[], T]
DiscreteDistribution: TypeAlias = dict[K, T]
Builder: TypeAlias = Callable[..., S]


# NOTE: `Generic[PolicyT]` rather than the PEP 695 `class BuiltSystem[PolicyT]` form
# so the package parses on Python 3.11 / PyPy 3.11 (target-version = py311); the
# NamedTuple + Generic multiple-inheritance is supported from CPython 3.11 onward.
class BuiltSystem(NamedTuple, Generic[PolicyT]):
    """Structured result returned by every ``build_*_system`` factory.

    A ``NamedTuple`` so the historical 5-target tuple unpacking still works
    (``psp, servers, shop_floor, router, policy = build_lumscor_system(...)``)
    and positional indexing is preserved (``result[2]`` is the shop floor),
    while also exposing named-field access (``result.policy``, ``result.router``).

    The ``PolicyT`` type parameter lets each builder annotate its precise policy
    type, e.g. ``BuiltSystem[LumsCor]`` for ``build_lumscor_system`` and
    ``BuiltSystem[None]`` for the push / no-policy builders.

    Attributes:
        psp: The Pre-Shop Pool, or ``None`` for push systems with no release control.
        servers: The processing servers (machines) on the shop floor, in order.
        shop_floor: The central ``ShopFloor`` orchestrator.
        router: The ``Router`` driving job arrivals and routing.
        policy: The wired release/dispatching policy instance (the *same* object
            installed during construction), or ``None`` when the builder wires
            plain callbacks rather than a policy object.
    """

    psp: PreShopPool | None
    servers: tuple[Server, ...]
    shop_floor: ShopFloor
    # `from __future__ import annotations` keeps this annotation a string at runtime,
    # so the forward reference to Router (imported only under TYPE_CHECKING to avoid a
    # circular import) is never evaluated when the class is created.
    router: Router
    policy: PolicyT


__all__ = [
    "BuiltSystem",
    "Builder",
    "DiscreteDistribution",
    "ProcessGenerator",
    "Sampler",
]
