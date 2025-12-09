from __future__ import annotations

from typing import Protocol, TypeVar

T = TypeVar("T", covariant=True)


class DistributionCallable(Protocol[T]):
    def __call__(self) -> T: ...
