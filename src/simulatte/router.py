"""Stochastic job generation and routing for discrete-event simulation.

This module provides the Router class, which continuously generates ProductionJob
instances using configurable probability distributions and routes them either to a
PreShopPool (pull system) or directly to the ShopFloor (push system).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Generator, Sequence
from typing import TYPE_CHECKING, NoReturn

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.shopfloor import ShopFloor
from simulatte.typing import DiscreteDistribution, Distribution

if TYPE_CHECKING:  # pragma: no cover
    from simpy.events import Timeout

    from simulatte.psp import PreShopPool
    from simulatte.server import Server


class Router:
    """Stochastic job generator that routes jobs through the simulation.

    The Router continuously generates ProductionJob instances at random intervals
    determined by the inter-arrival distribution. Each job is assigned a randomly
    selected SKU, a routing through servers, and processing times sampled from
    configured distributions.

    Jobs are routed based on system configuration:
    - **Push system** (psp=None): Jobs go directly to the ShopFloor
    - **Pull system** (psp set): Jobs queue in the PreShopPool until released

    Upon instantiation, the Router registers itself as a SimPy process that runs
    for the duration of the simulation.
    """

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
        due_date_offset_distribution: dict[str, Distribution[float]],
        priority_policies: Callable[[ProductionJob, Server], float] | None = None,
    ) -> None:
        """Initialize the Router and start the job generation process.

        Args:
            env: The simulation environment.
            shopfloor: ShopFloor instance managing job flow and WIP tracking.
            servers: Sequence of all available Server instances in the system.
            psp: PreShopPool for pull systems, or None for push systems where jobs
                go directly to the ShopFloor.
            inter_arrival_distribution: Callable returning the time until the next
                job arrival (e.g., ``lambda: random.expovariate(1.0)``).
            sku_distributions: Mapping from SKU names to probability weights for
                random SKU selection (e.g., ``{"A": 0.5, "B": 0.3, "C": 0.2}``).
            sku_routings: Mapping from SKU to a callable that returns the server
                routing sequence for that SKU.
            sku_service_times: Nested mapping ``{sku: {server: distribution}}`` where
                each distribution is a callable returning the processing time.
            due_date_offset_distribution: Mapping from SKU to a callable returning the
                offset used to compute due date (``due_date = now + offset``).
            priority_policies: Optional callable ``(job, server) -> float`` for
                computing job priority at each server.

        Example:
            >>> router = Router(
            ...     env=env,
            ...     shopfloor=shop_floor,
            ...     servers=servers,
            ...     psp=None,  # Push system
            ...     inter_arrival_distribution=lambda: random.expovariate(1.0),
            ...     sku_distributions={"F1": 1.0},
            ...     sku_routings={"F1": lambda: servers},
            ...     sku_service_times={"F1": {s: lambda: 2.0 for s in servers}},
            ...     due_date_offset_distribution={"F1": lambda: 30.0},
            ... )
        """
        self.env = env
        self.shopfloor = shopfloor
        self.servers = servers
        self.psp = psp

        self.inter_arrival_distribution = inter_arrival_distribution
        self.sku_distributions = sku_distributions
        self.sku_routings = sku_routings
        self.sku_service_times = sku_service_times
        self.due_date_offset_distribution = due_date_offset_distribution
        self.priority_policies = priority_policies

        self.env.process(self.generate_job())

    def generate_job(self) -> Generator[Timeout, None, NoReturn]:
        """Infinite generator that creates and routes jobs at random intervals.

        This method runs as a SimPy process for the simulation's duration. On each
        iteration it:

        1. Waits for the inter-arrival time
        2. Samples a random SKU based on configured weights
        3. Generates a routing and processing times for the selected SKU
        4. Creates a ProductionJob with computed due date
        5. Routes the job to PSP (if configured) or directly to ShopFloor

        Yields:
            simpy.Timeout: Pauses the process until the next job arrival.
        """
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
            waiting_time = self.due_date_offset_distribution[sku]()

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
