from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, cast

from simulatte.environment import Environment
from simulatte.products import Product
from simulatte.typings import ProcessGenerator
from simulatte.unitload.pallet import PalletMultiProduct
from simulatte.utils import EnvMixin, IdentifiableMixin, as_process

if TYPE_CHECKING:
    from simulatte.operations import FeedingOperation


class TimedMixin:
    """Shared helpers for start/end timestamps."""

    def __init__(self, *, env: Environment) -> None:
        self._start_time: float = 0.0
        self._end_time: float | None = None
        self.env = env

    @property
    def lead_time(self) -> float | None:
        if self._end_time is None:
            return None
        return self._end_time - self._start_time

    def started(self) -> None:
        self._start_time = float(self.env.now)

    def completed(self) -> None:
        self._end_time = float(self.env.now)


class OrderLine(IdentifiableMixin, EnvMixin, TimedMixin):
    """
    Smallest unit of demand: a product with a number of cases.

    The previous hierarchy (Case -> Product -> Layer -> Pallet) is collapsed
    into this flat structure. Each line knows its parent pallet and any
    feeding operations assigned to it.
    """

    def __init__(self, product: Product, n_cases: int, *, env: Environment) -> None:
        if n_cases < 1:
            raise ValueError("OrderLine requires at least one case")

        IdentifiableMixin.__init__(self)
        EnvMixin.__init__(self, env=env)
        TimedMixin.__init__(self, env=env)

        self.product = product
        self.n_cases = n_cases
        self.feeding_operations: list[FeedingOperation] = []
        self.parent: PalletRequest | None = None

    @as_process
    def wait_for_feeding_operations(self) -> ProcessGenerator[list[FeedingOperation]]:
        """Yield until at least one feeding operation is attached."""

        while not self.feeding_operations:
            yield self.env.timeout(1)
        return self.feeding_operations


class PalletOrder(IdentifiableMixin, EnvMixin, TimedMixin):
    """
    Flat pallet request made of OrderLines. Preserves timing info and exposes
    aggregated feeding operations.
    """

    def __init__(self, order_lines: Sequence[OrderLine | tuple[Product, int]], *, env: Environment) -> None:
        if not order_lines:
            raise ValueError("PalletOrder requires at least one OrderLine")

        IdentifiableMixin.__init__(self)
        EnvMixin.__init__(self, env=env)
        TimedMixin.__init__(self, env=env)

        normalized: list[OrderLine] = []
        for line in order_lines:
            if isinstance(line, OrderLine):
                normalized.append(line)
            else:
                product, n_cases = cast(tuple[Product, int], line)
                normalized.append(OrderLine(product=product, n_cases=n_cases, env=env))

        self.order_lines: tuple[OrderLine, ...] = tuple(normalized)
        for line in self.order_lines:
            line.parent = self

        self.unit_load = PalletMultiProduct()

    def __iter__(self) -> Iterator[OrderLine]:
        return iter(self.order_lines)

    @property
    def n_cases(self) -> int:
        return sum(line.n_cases for line in self.order_lines)

    @property
    def feeding_operations(self) -> tuple[FeedingOperation, ...]:
        return tuple(fo for line in self.order_lines for fo in line.feeding_operations)

    @property
    def oos_delay(self) -> float:
        fos = self.feeding_operations
        delay = 0.0
        i = 1
        j = 0
        while i < len(fos):
            t1 = getattr(fos[j].log, "finished_agv_trip_to_cell", None)
            t2 = getattr(fos[i].log, "finished_agv_trip_to_cell", None)
            if t2 is not None and t1 is not None and t2 < t1:
                delay += t1 - t2
            i += 1
            j += 1
        return delay

    @property
    def workload(self) -> int:
        """Basic workload estimate; count lines."""

        return len(self.order_lines)


# Backwards-compatible aliases so controllers/tests need minimal touch
PalletRequest = PalletOrder
ProductRequest = OrderLine

__all__ = ["OrderLine", "PalletOrder", "PalletRequest", "ProductRequest"]
