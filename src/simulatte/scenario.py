"""Scenario: a reusable description of a shop environment and its order stream.

A Scenario captures everything about the *environment* — shop type (routing
structure), machine count, arrival process, service-time distribution, and
due-date rule — independent of the *control method* (immediate, LumsCor, DRACO,
…). Any ``build_*_system`` in :mod:`simulatte.builders` accepts a Scenario, so
methods and shops vary independently.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from simulatte.distributions import (
    Distribution,
    TruncatedErlang,
    arrival_rate_for_utilization,
    general_flow_shop_routing,
    pure_flow_shop_routing,
    pure_job_shop_routing,
    truncated_2erlang,
    twk_due_date,
)
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import CurrentWorkLoadCollector, ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simulatte.environment import Environment
    from simulatte.psp import PreShopPool


class ShopType(Enum):
    """Standard workload-control benchmark shop types (by routing directedness)."""

    PJS = "pure_job_shop"  # random length U[1,M], undirected (random order)
    GFS = "general_flow_shop"  # random length U[1,M], directed (sorted by index)
    PFS = "pure_flow_shop"  # fixed length M, fully directed (all machines, fixed order)


_ROUTING = {
    ShopType.PJS: pure_job_shop_routing,
    ShopType.GFS: general_flow_shop_routing,
    ShopType.PFS: pure_flow_shop_routing,
}

_DEFAULT_SERVICE_TIME = TruncatedErlang(rate=2.0, shape=2, max_value=4.0)


@dataclass(frozen=True)
class SkuFamily:
    """One product family: its routing, service-time distribution, due-date, and mix weight."""

    name: str = "F1"
    weight: float = 1.0
    service_time: Distribution = _DEFAULT_SERVICE_TIME
    routing_factory: Callable[[Sequence[Server]], Callable[[], Sequence[Server]]] | None = None
    expected_routing_length: float | None = None
    due_date_offset: Distribution | None = None
    twk_allowance_factor: float | None = None

    def __post_init__(self) -> None:
        if self.weight <= 0:
            msg = f"weight must be positive, got {self.weight}"
            raise ValueError(msg)
        if self.routing_factory is not None and self.expected_routing_length is None:
            msg = "SkuFamily with a custom routing_factory must set expected_routing_length."
            raise ValueError(msg)
        if self.expected_routing_length is not None and self.expected_routing_length <= 0:
            msg = f"expected_routing_length must be positive, got {self.expected_routing_length}"
            raise ValueError(msg)

    def routing_for(self, shop_type: ShopType) -> Callable[[Sequence[Server]], Callable[[], Sequence[Server]]]:
        """This family's routing factory (custom override or the shop-type default)."""
        return self.routing_factory or _ROUTING[shop_type]

    def mean_routing_length(self, shop_type: ShopType, n_servers: int) -> float:
        """Expected operations per order, E[L] (explicit override, else shop-type formula)."""
        if self.expected_routing_length is not None:
            return self.expected_routing_length
        if shop_type is ShopType.PFS:
            return float(n_servers)
        return (n_servers + 1) / 2


@dataclass(frozen=True)
class Scenario:
    """Immutable description of a shop environment and its order stream."""

    shop_type: ShopType = ShopType.PJS
    n_servers: int = 6
    target_utilization: float = 0.90
    service_rate: float = 2.0
    service_max: float = 4.0
    due_date_offset_range: tuple[float, float] = (30.0, 45.0)
    twk_allowance_factor: float | None = None
    sku: str = "F1"
    routing_factory: Callable[[Sequence[Server]], Callable[[], Sequence[Server]]] | None = None
    arrival_rate: float | None = None
    expected_routing_length: float | None = None

    @classmethod
    def pure_job_shop(cls, **overrides: object) -> Scenario:
        """Pure Job Shop preset (undirected routing)."""
        return cls(shop_type=ShopType.PJS, **overrides)  # type: ignore[arg-type]

    @classmethod
    def general_flow_shop(cls, **overrides: object) -> Scenario:
        """General Flow Shop preset (directed/sorted routing)."""
        return cls(shop_type=ShopType.GFS, **overrides)  # type: ignore[arg-type]

    @classmethod
    def pure_flow_shop(cls, **overrides: object) -> Scenario:
        """Pure Flow Shop preset (all machines, fixed order)."""
        return cls(shop_type=ShopType.PFS, **overrides)  # type: ignore[arg-type]

    @property
    def mean_routing_length(self) -> float:
        """Expected number of operations per order, ``E[L]``."""
        if self.expected_routing_length is not None:
            return self.expected_routing_length
        if self.routing_factory is not None:
            msg = "Scenario with a custom routing_factory must set expected_routing_length or arrival_rate."
            raise ValueError(msg)
        if self.shop_type is ShopType.PFS:
            return float(self.n_servers)
        return (self.n_servers + 1) / 2

    def routing_for(self) -> Callable[[Sequence[Server]], Callable[[], Sequence[Server]]]:
        """The routing factory for this scenario (custom override or shop-type default)."""
        return self.routing_factory or _ROUTING[self.shop_type]

    def resolved_arrival_rate(self) -> float:
        """The exponential arrival rate (explicit override, else derived from utilization)."""
        if self.arrival_rate is not None:
            return self.arrival_rate
        return arrival_rate_for_utilization(
            self.target_utilization,
            n_servers=self.n_servers,
            mean_routing_length=self.mean_routing_length,
            mean_processing_time=2.0 / self.service_rate,
        )

    def build_floor(
        self,
        env: Environment,
        *,
        collect_workload: bool = False,
        collect_time_series: bool = False,
        retain_job_history: bool = False,
    ) -> tuple[ShopFloor, tuple[Server, ...]]:
        """Create the ShopFloor and ``n_servers`` single-capacity servers."""
        shop_floor = ShopFloor(
            env=env,
            time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
        )
        servers = tuple(
            Server(
                env=env,
                capacity=1,
                shopfloor=shop_floor,
                collect_time_series=collect_time_series,
                retain_job_history=retain_job_history,
            )
            for _ in range(self.n_servers)
        )
        return shop_floor, servers

    def build_router(
        self,
        env: Environment,
        shop_floor: ShopFloor,
        servers: Sequence[Server],
        *,
        psp: PreShopPool | None,
        priority_policies: Callable[..., float] | None = None,
    ) -> Router:
        """Assemble the Router: derived arrival rate, routing factory, 2-Erlang service times, due dates."""
        rate = self.resolved_arrival_rate()
        due_date_rule = (
            {self.sku: twk_due_date(self.twk_allowance_factor)} if self.twk_allowance_factor is not None else None
        )
        factory = self.routing_for()
        return Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=psp,
            inter_arrival_distribution=lambda: random.expovariate(rate),
            sku_distributions={self.sku: 1},
            sku_routings={self.sku: factory(servers)},
            sku_service_times={
                self.sku: {
                    server: lambda: truncated_2erlang(lam=self.service_rate, max_value=self.service_max)
                    for server in servers
                },
            },
            due_date_offset_distribution={self.sku: lambda: random.uniform(*self.due_date_offset_range)},
            due_date_rule=due_date_rule,
            priority_policies=priority_policies,
        )
