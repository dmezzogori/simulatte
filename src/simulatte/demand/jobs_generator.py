from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from simulatte.demand.customer_order import CustomerOrder
from simulatte.demand.shift import Shift
from simulatte.products import ProductsGenerator
from simulatte.protocols.distribution_callable import DistributionCallable
from simulatte.protocols.job import Job


class JobsGenerator(Protocol):
    """
    A protocol for a generator of jobs.

    Attributes:
        n_days: number of days to be generated
        n_shift_per_day: number of shifts in each day
        orders_per_shift_distribution: a distribution from which to sample the number of orders in each shift
        jobs_per_order_distribution: a distribution from which to sample the number of jobs in each order
        products_generator: a products generator from which to sample
    """

    n_days: int
    n_shift_per_day: int
    orders_per_shift_distribution: DistributionCallable[int]
    jobs_per_order_distribution: DistributionCallable[int]
    products_generator: ProductsGenerator
    _shifts: tuple[Shift, ...] | None = None

    def _generate_jobs(self) -> Iterator[Job]:
        """
        Method which create the list of pallets within a customer order
        """
        ...

    def __iter__(self) -> Iterator[Shift]:
        """For each day and shift, generate the customer orders list"""

        if self._shifts is None:
            self._shifts = tuple(
                Shift(
                    day=day,
                    shift=shift,
                    customer_orders=tuple(
                        CustomerOrder(day=day, shift=shift, jobs=tuple(self._generate_jobs()))
                        for _ in range(self.orders_per_shift_distribution())
                    ),
                )
                for day in range(self.n_days)
                for shift in range(self.n_shift_per_day)
            )

        return iter(self._shifts)
