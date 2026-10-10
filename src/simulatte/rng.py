"""Random-number streams and sampler binding (spec §8).

Every source of randomness draws from a named stream of its environment: ``env.rng(name)`` returns a
:class:`random.Random` seeded with :func:`derive_seed` of the environment seed and the stream name, so
streams are independent of one another and of the order in which they are created.

Components accept the values that drive their randomness in three *binding kinds* and resolve them with
``env.bind(value, kind=..., stream=..., owner=...)``:

=========== ============================ ===============================================================
Kind        Callback shape               Managed forms
=========== ============================ ===============================================================
scalar      ``() -> float``              a description with ``sampler(rng)``, a number
routing     ``() -> Sequence[Server]``   a routing description with ``sampler(rng)``, a fixed sequence
contextual  ``(*context) -> float``      a description with ``sampler(rng)``, a number (context ignored)
=========== ============================ ===============================================================

Any other callable is *opaque*: it is used unchanged and its owner is recorded in
``env.opaque_sampler_owners``.
"""

from __future__ import annotations

import hashlib
import numbers
import random
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol, TypeAlias, TypeVar, runtime_checkable

__all__ = [
    "RNG_DERIVATION",
    "BindingKind",
    "SamplerDescription",
    "derive_seed",
]

RNG_DERIVATION = "simulatte-rng-v1"
"""Version tag of the stream-seed derivation; part of every derived seed."""

BindingKind: TypeAlias = Literal["scalar", "routing", "contextual"]
_KINDS = frozenset({"scalar", "routing", "contextual"})

T_co = TypeVar("T_co", covariant=True)


@runtime_checkable
class SamplerDescription(Protocol[T_co]):
    """A description of a random variate that builds samplers drawing from a given stream."""

    def sampler(self, rng: random.Random) -> Callable[[], T_co]: ...


def derive_seed(seed: int, name: str) -> int:
    """Seed of stream `name` in an environment seeded with `seed` (a 128-bit integer)."""
    data = f"{RNG_DERIVATION}\0{seed}\0{name}".encode()
    return int.from_bytes(hashlib.blake2b(data, digest_size=16).digest(), "big")


class DrawCounter:
    """Number of draws taken from the counting streams of one environment (debug mode)."""

    __slots__ = ("draws",)

    def __init__(self) -> None:
        self.draws = 0


class CountingRandom(random.Random):
    """A :class:`random.Random` that counts its draws in a :class:`DrawCounter`.

    It overrides both primitives (``random`` and ``getrandbits``), so the derived methods keep the base
    class algorithms and produce the same values as a plain ``random.Random`` with the same seed.
    """

    def __init__(self, seed: int, counter: DrawCounter) -> None:
        self._counter = counter
        super().__init__(seed)

    def random(self) -> float:
        self._counter.draws += 1
        return super().random()

    def getrandbits(self, k: int, /) -> int:
        self._counter.draws += 1
        return super().getrandbits(k)


def resolve_binding(
    value: object, *, kind: str, stream: Callable[[], random.Random]
) -> tuple[Callable[..., Any], bool]:
    """Resolve `value` into a callback of the shape of `kind`.

    `stream` returns the RNG stream and is called only for descriptions. Returns the callback and whether
    it is opaque. Raises `ValueError` for an unknown kind and `TypeError` for a value of no accepted form.
    """
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {sorted(_KINDS)}, got {kind!r}")
    if isinstance(value, type) and hasattr(value, "sampler"):
        raise TypeError(f"cannot bind the description class {value.__name__}; pass an instance")
    if isinstance(value, SamplerDescription):
        sample = value.sampler(stream())
        if kind == "contextual":
            return _ignoring_context(sample), False
        return sample, False
    if kind != "routing" and isinstance(value, numbers.Real) and not isinstance(value, bool):
        constant = float(value)
        if kind == "contextual":
            return _ignoring_context(lambda: constant), False
        return (lambda: constant), False
    if callable(value):
        return value, True
    if kind == "routing" and isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        fixed = tuple(value)
        return (lambda: fixed), False
    raise TypeError(f"cannot bind {type(value).__name__} as a {kind} sampler")


def _ignoring_context(sample: Callable[[], Any]) -> Callable[..., Any]:
    def contextual(*context: object) -> Any:
        return sample()

    return contextual
