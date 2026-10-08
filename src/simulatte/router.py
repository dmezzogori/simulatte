"""Stochastic job generation and routing for discrete-event simulation.

This module provides the Router class, which continuously generates ProductionJob
instances using configurable probability distributions and routes them either to a
PreShopPool (pull system) or directly to the ShopFloor (push system).
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn, TypeAlias

from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.rng import SamplerDescription
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from simpy.events import Timeout

    from simulatte.psp import PreShopPool
    from simulatte.server import Server

ScalarSource: TypeAlias = "SamplerDescription[float] | float | Callable[[], float]"
"""A scalar binding: a distribution description, a number, or an opaque ``() -> float`` callable."""

RoutingSource: TypeAlias = "SamplerDescription[Sequence[Server]] | Sequence[Server] | Callable[[], Sequence[Server]]"
"""A routing binding: a routing description, a fixed server sequence, or an opaque callable."""


class Router(Entity, kind="router"):
    """Stochastic job generator that routes jobs through the simulation.

    The Router continuously generates ProductionJob instances at random intervals
    determined by the inter-arrival distribution. Each job is assigned a randomly
    selected SKU, a routing through servers, and processing times sampled from
    configured distributions.

    Jobs are routed based on system configuration:
    - **Push system** (psp=None): Jobs go directly to the ShopFloor
    - **Pull system** (psp set): Jobs queue in the PreShopPool until released

    Every random draw comes from a named stream of the environment (``env.rng``):
    ``<id>/interarrival``, ``<id>/sku``, ``<id>/routing/<sku>``,
    ``<id>/service/<sku>/<server id>`` and ``<id>/due/<sku>``. Distribution and
    routing descriptions, numbers and fixed server sequences are bound to these
    streams with ``env.bind``; any other callable is used unchanged and recorded
    as opaque in ``env.opaque_sampler_owners``.

    Upon instantiation, the Router registers itself as a SimPy process that runs
    for the duration of the simulation.
    """

    state_schema: ClassVar[StateSchema] = StateSchema({"shopfloor": FieldSpec("str", nullable=True)})

    def __init__(
        self,
        *,
        env: Environment,
        shopfloor: ShopFloor,
        servers: Sequence[Server],
        psp: PreShopPool | None,
        inter_arrival_distribution: ScalarSource,
        sku_distributions: Mapping[str, float],
        sku_routings: Mapping[str, RoutingSource],
        sku_service_times: Mapping[str, Mapping[Server, ScalarSource]],
        due_date_offset_distribution: Mapping[str, ScalarSource],
        priority_policies: Callable[[ProductionJob, Server], float] | None = None,
        due_date_rule: dict[str, Callable[[Sequence[float]], float]] | None = None,
        name: str | None = None,
        label: str | None = None,
    ) -> None:
        """Initialize the Router and start the job generation process.

        Args:
            env: The simulation environment.
            shopfloor: ShopFloor instance managing job flow and WIP tracking.
            servers: Sequence of all available Server instances in the system.
            psp: PreShopPool for pull systems, or None for push systems where jobs
                go directly to the ShopFloor.
            inter_arrival_distribution: Time until the next job arrival: a
                distribution description (e.g. ``Exponential(1.0)``), a number, or
                a ``() -> float`` callable.
            sku_distributions: Mapping from SKU names to probability weights for
                random SKU selection (e.g., ``{"A": 0.5, "B": 0.3, "C": 0.2}``).
            sku_routings: Mapping from SKU to its routing: a routing description
                (e.g. ``PureJobShopRouting(servers)``), a fixed server sequence, or
                a ``() -> Sequence[Server]`` callable.
            sku_service_times: Nested mapping ``{sku: {server: time}}`` where each
                time is a distribution description, a number or a callable. A
                description shared across servers yields one independent sampler
                per server.
            due_date_offset_distribution: Mapping from SKU to the offset used to
                compute the due date (``due_date = now + offset``), in the same
                forms as the service times.
            priority_policies: Optional callable ``(job, server) -> float`` for
                computing job priority at each server.
            due_date_rule: Optional per-SKU mapping to a callable
                ``(processing_times) -> float`` that derives the due-date offset
                from the job's sampled operation processing times (its total work
                content). When a SKU has an entry here it **takes precedence** over
                ``due_date_offset_distribution`` for that SKU; otherwise the flat
                offset is used. This enables work-content due-date rules such as
                Total Work Content (TWK): ``due_date = now + K * sum(p_ij)``.
            name: Optional id of the router; defaults to ``router-<n>``.
            label: Optional display label; defaults to the id.

        Example:
            >>> router = Router(
            ...     env=env,
            ...     shopfloor=shop_floor,
            ...     servers=servers,
            ...     psp=None,  # Push system
            ...     inter_arrival_distribution=Exponential(1.0),
            ...     sku_distributions={"F1": 1.0},
            ...     sku_routings={"F1": servers},
            ...     sku_service_times={"F1": {s: 2.0 for s in servers}},
            ...     due_date_offset_distribution={"F1": 30.0},
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
        self.due_date_rule = due_date_rule

        env.entities.attach(self, name=name, label=label)
        rid = self.id
        self._inter_arrival = env.bind(
            inter_arrival_distribution, kind="scalar", stream=f"{rid}/interarrival", owner=rid
        )
        self._skus = tuple(sku_distributions.keys())
        self._sku_weights = tuple(sku_distributions.values())
        self._sku_rng = env.rng(f"{rid}/sku")
        self._routings = {
            sku: env.bind(routing, kind="routing", stream=f"{rid}/routing/{sku}", owner=rid)
            for sku, routing in sku_routings.items()
        }
        self._service_times = {
            sku: {
                server: env.bind(time, kind="scalar", stream=f"{rid}/service/{sku}/{server.id}", owner=rid)
                for server, time in times.items()
            }
            for sku, times in sku_service_times.items()
        }
        self._due_date_offsets = {
            sku: env.bind(offset, kind="scalar", stream=f"{rid}/due/{sku}", owner=rid)
            for sku, offset in due_date_offset_distribution.items()
        }
        self.env.process(self.generate_job())

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: the owning shop floor's id."""
        return {"shopfloor": self.shopfloor.id, "label": self.label}

    def generate_job(self) -> Generator[Timeout, None, NoReturn]:
        """Infinite generator that creates and routes jobs at random intervals.

        This method runs as a SimPy process for the simulation's duration. On each
        iteration it:

        1. Waits for the inter-arrival time
        2. Samples a random SKU based on configured weights
        3. Generates a routing and processing times for the selected SKU
        4. Creates a ProductionJob with computed due date
        5. Routes the job to PSP (if configured) or directly to ShopFloor

        Each draw comes from its own stream, in this order per job: inter-arrival
        time (before the timeout), SKU, routing, one service time per operation,
        due-date offset.

        Yields:
            simpy.Timeout: Pauses the process until the next job arrival.
        """
        choices = self._sku_rng.choices
        skus, weights = self._skus, self._sku_weights
        while True:
            inter_arrival_time = self._inter_arrival()
            yield self.env.timeout(inter_arrival_time)

            sku = choices(skus, weights=weights, k=1)[0]

            routing = self._routings[sku]()
            service_times_of = self._service_times[sku]
            service_times = tuple(service_times_of[server]() for server in routing)

            # A per-SKU due_date_rule (e.g. Total Work Content) derives the offset
            # from the job's total work content and takes precedence over the flat
            # due_date_offset_distribution; otherwise fall back to the flat offset.
            rule = None if self.due_date_rule is None else self.due_date_rule.get(sku)
            if rule is not None:
                waiting_time = rule(service_times)
            else:
                waiting_time = self._due_date_offsets[sku]()

            job = ProductionJob(
                env=self.env,
                sku=sku,
                servers=routing,
                processing_times=service_times,
                due_date=self.env.now + waiting_time,
                priority_policy=self.priority_policies,
            )

            self.env.debug(
                f"Job {job.id} created",
                component="Router",
                job_id=job.id,
                sku=sku,
                routing_length=len(routing),
                due_date=job.due_date,
                total_processing_time=sum(service_times),
            )

            if self.psp is not None:
                self.env.debug(
                    f"Job {job.id} routed to PSP",
                    component="Router",
                    job_id=job.id,
                    destination="PSP",
                )
                self.psp.add(job)
            else:
                self.env.debug(
                    f"Job {job.id} routed to ShopFloor",
                    component="Router",
                    job_id=job.id,
                    destination="ShopFloor",
                )
                self.shopfloor.add(job)
