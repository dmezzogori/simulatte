from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from simulatte.protocols import Job


@dataclass
class CustomerOrder:
    """
    Represents a customer order.

    Attributes:
        day: an integer representing the day in which the order should be processed
        shift: an integer representing the shift number within the day in which the order should be processed
        jobs: a collection of jobs to be processed to satisfy the order
    """

    day: int
    shift: int
    jobs: Collection[Job]
