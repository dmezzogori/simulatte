"""Job generator and routing logic."""

from __future__ import annotations

import random
from collections.abc import Callable, Generator, Sequence
from typing import TYPE_CHECKING, NoReturn

from simulatte.environment import Environment
from simulatte.job import Job
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from simpy.events import Timeout

    from simulatte.psp import PreShopPool
    from simulatte.server import Server

type Distribution[T] = Callable[[], T]
type DiscreteDistribution[K, T] = dict[K, T]


class Router:
    """Generates jobs from distributions and routes them to PSP or shopfloor."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        servers: Sequence[Server],
        psp: PreShopPool | None,
        inter_arrival_distribution: Distribution[float],
        family_distributions: DiscreteDistribution[str, float],
        family_routings: dict[str, Callable[[], Sequence[Server]]],
        family_service_times: dict[
            str,
            DiscreteDistribution[Server, Distribution[float]],
        ],
        waiting_time_distribution: dict[str, Distribution[float]],
        priority_policies: Callable[[Job, Server], float] | None = None,
    ) -> None:
        self.env = Environment()
        self.servers = servers
        self.psp = psp
        self.shopfloor = ShopFloor()

        self.inter_arrival_distribution = inter_arrival_distribution
        self.family_distributions = family_distributions
        self.family_routings = family_routings
        self.family_service_times = family_service_times
        self.waiting_time_distribution = waiting_time_distribution
        self.priority_policies = priority_policies

        self.env.process(self.generate_job())

    def generate_job(self) -> Generator[Timeout, None, NoReturn]:
        while True:
            inter_arrival_time = self.inter_arrival_distribution()
            yield self.env.timeout(inter_arrival_time)

            family = random.choices(  # noqa: S311
                tuple(self.family_distributions.keys()),
                weights=tuple(self.family_distributions.values()),
                k=1,
            )[0]

            routing = self.family_routings[family]()
            service_times = tuple(self.family_service_times[family][server]() for server in routing)
            waiting_time = self.waiting_time_distribution[family]()

            job = Job(
                family=family,
                servers=routing,
                processing_times=service_times,
                due_date=self.env.now + waiting_time,
                priority_policy=self.priority_policies,
            )

            if self.psp is not None:
                self.psp.add(job)
            else:
                self.shopfloor.add(job)
