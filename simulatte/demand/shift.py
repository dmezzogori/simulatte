from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from simulatte.demand.customer_order import CustomerOrder
from simulatte.protocols import Job


@dataclass
class Shift:
    """
    Represent a shift, during which a given set of customer orders must be satisfied.

    Attributes:
        day: an integer representing the day of the shift
        shift: an integer representing the number of the shift
        customer_orders: a collection of customer orders to be satisfied during the shift
    """

    day: int
    shift: int
    customer_orders: Collection[CustomerOrder]

    @property
    def jobs(self) -> Iterable[Job]:
        for customer_order in self.customer_orders:
            yield from customer_order.jobs
