"""Scenario: a reusable description of a shop environment and its order stream.

A Scenario captures everything about the *environment* — shop type (routing
structure), machine count, arrival process, service-time distribution, and
due-date rule — independent of the *control method* (immediate, LumsCor, DRACO,
…). Any ``build_*_system`` in :mod:`simulatte.builders` accepts a Scenario, so
methods and shops vary independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from simulatte.distributions import (
    arrival_rate_for_utilization,
    general_flow_shop_routing,
    pure_flow_shop_routing,
    pure_job_shop_routing,
)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simulatte.server import Server


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
