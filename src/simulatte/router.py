"""Job generator and routing logic."""

from __future__ import annotations

import random
from collections.abc import Callable, Generator, Sequence
from typing import TYPE_CHECKING, NoReturn

from simulatte.environment import Environment
from simulatte.job import ProductionJob
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
        env: Environment,
        shopfloor: ShopFloor,
        servers: Sequence[Server],
        psp: PreShopPool | None,
        inter_arrival_distribution: Distribution[float],
        sku_distributions: DiscreteDistribution[str, float],
        sku_routings: dict[str, Callable[[], Sequence[Server]]],
        sku_service_times: dict[
            str,
            DiscreteDistribution[Server, Distribution[float]],
        ],
        waiting_time_distribution: dict[str, Distribution[float]],
        priority_policies: Callable[[ProductionJob, Server], float] | None = None,
    ) -> None:
        self.env = env
        self.shopfloor = shopfloor
        self.servers = servers
        self.psp = psp

        self.inter_arrival_distribution = inter_arrival_distribution
        self.sku_distributions = sku_distributions
        self.sku_routings = sku_routings
        self.sku_service_times = sku_service_times
        self.waiting_time_distribution = waiting_time_distribution
        self.priority_policies = priority_policies

        self.env.process(self.generate_job())

    def generate_job(self) -> Generator[Timeout, None, NoReturn]:
        while True:
            inter_arrival_time = self.inter_arrival_distribution()
            yield self.env.timeout(inter_arrival_time)

            sku = random.choices(  # noqa: S311
                tuple(self.sku_distributions.keys()),
                weights=tuple(self.sku_distributions.values()),
                k=1,
            )[0]

            routing = self.sku_routings[sku]()
            service_times = tuple(self.sku_service_times[sku][server]() for server in routing)
            waiting_time = self.waiting_time_distribution[sku]()

            job = ProductionJob(
                env=self.env,
                sku=sku,
                servers=routing,
                processing_times=service_times,
                due_date=self.env.now + waiting_time,
                priority_policy=self.priority_policies,
            )

            self.env.debug(
                f"Job {job.id[:8]} created",
                component="Router",
                job_id=job.id,
                sku=sku,
                routing_length=len(routing),
                due_date=job.due_date,
                total_processing_time=sum(service_times),
            )

            if self.psp is not None:
                self.env.debug(
                    f"Job {job.id[:8]} routed to PSP",
                    component="Router",
                    job_id=job.id,
                    destination="PSP",
                )
                self.psp.add(job)
            else:
                self.env.debug(
                    f"Job {job.id[:8]} routed to ShopFloor",
                    component="Router",
                    job_id=job.id,
                    destination="ShopFloor",
                )
                self.shopfloor.add(job)
