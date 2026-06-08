"""Scenario: a reusable description of a shop environment and its order stream.

A Scenario captures everything about the *environment* — shop type (routing
structure), machine count, arrival process, per-family service-time
distributions, and per-family due-date offsets/rules — independent of the
*control method* (immediate, LumsCor, DRACO, …). Any ``build_*_system`` in
:mod:`simulatte.builders` accepts a Scenario, so methods and shops vary
independently.

Product-mix heterogeneity is expressed via the :class:`SkuFamily` entries in
``Scenario.families``: each family carries its own service-time distribution,
due-date offset, due-date rule, and mix weight, so a single Scenario can model
several product types arriving on one shared stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from simulatte.distributions import (
    Distribution,
    Exponential,
    TruncatedErlang,
    Uniform,
    arrival_rate_for_utilization,
    general_flow_shop_routing,
    pure_flow_shop_routing,
    pure_job_shop_routing,
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
    families: tuple[SkuFamily, ...] = (SkuFamily(),)
    due_date_offset: Distribution = Uniform(low=30.0, high=45.0)
    arrival_process: Callable[[float], Callable[[], float]] = Exponential
    arrival_rate: float | None = None

    def __post_init__(self) -> None:
        if not self.families:
            msg = "Scenario must have at least one SkuFamily."
            raise ValueError(msg)
        names = [f.name for f in self.families]
        if len(set(names)) != len(names):
            msg = f"SkuFamily names must be unique, got {names}"
            raise ValueError(msg)
        if self.n_servers < 1:
            msg = f"n_servers must be >= 1, got {self.n_servers}"
            raise ValueError(msg)
        if not 0 < self.target_utilization <= 1:
            msg = f"target_utilization must be in (0, 1], got {self.target_utilization}"
            raise ValueError(msg)

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

    @classmethod
    def single(
        cls,
        *,
        service_time: Distribution | None = None,
        due_date_offset: Distribution | None = None,
        twk_allowance_factor: float | None = None,
        name: str = "F1",
        **shop: object,
    ) -> Scenario:
        """Convenience for the common one-product case: build a single-family Scenario.

        ``name`` is always forwarded (it has a default), while ``service_time``,
        ``due_date_offset``, and ``twk_allowance_factor`` are forwarded only when
        non-None, so ``SkuFamily``'s own defaults fill the rest. ``**shop``
        forwards shop-level kwargs (``shop_type``, ``n_servers``,
        ``target_utilization``, ``arrival_rate``, ...).
        """
        family_kwargs = {
            k: v
            for k, v in {
                "name": name,
                "service_time": service_time,
                "due_date_offset": due_date_offset,
                "twk_allowance_factor": twk_allowance_factor,
            }.items()
            if v is not None
        }
        return cls(families=(SkuFamily(**family_kwargs),), **shop)  # type: ignore[arg-type]

    def resolved_arrival_rate(self) -> float:
        """The exponential arrival rate (explicit override, else mix-weighted derivation)."""
        if self.arrival_rate is not None:
            return self.arrival_rate
        total_weight = sum(f.weight for f in self.families)
        expected_work = sum(
            (f.weight / total_weight) * f.mean_routing_length(self.shop_type, self.n_servers) * f.service_time.mean
            for f in self.families
        )
        # expected_work already encodes Σ wᵢ·E[Lᵢ]·E[pᵢ] (work per job), so we pass it as the
        # length arg with an identity mean_processing_time=1.0; the helper reduces to ρ·M / expected_work.
        return arrival_rate_for_utilization(
            self.target_utilization,
            n_servers=self.n_servers,
            mean_routing_length=expected_work,
            mean_processing_time=1.0,
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
        """Assemble the Router from the family mix: arrival process, per-family routing,
        service-time distributions, and due-date offsets/rules."""
        rate = self.resolved_arrival_rate()
        due_date_rule = {
            f.name: twk_due_date(f.twk_allowance_factor) for f in self.families if f.twk_allowance_factor is not None
        } or None
        return Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=psp,
            inter_arrival_distribution=self.arrival_process(rate),
            sku_distributions={f.name: f.weight for f in self.families},
            sku_routings={f.name: f.routing_for(self.shop_type)(servers) for f in self.families},
            sku_service_times={f.name: {server: f.service_time for server in servers} for f in self.families},
            due_date_offset_distribution={f.name: (f.due_date_offset or self.due_date_offset) for f in self.families},
            due_date_rule=due_date_rule,
            priority_policies=priority_policies,
        )
