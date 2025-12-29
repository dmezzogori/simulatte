"""Builders for material handling system configurations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.experimental.agv import AGV
from simulatte.experimental.materials import MaterialCoordinator
from simulatte.experimental.warehouse import Warehouse
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.experimental.typing import MaterialSystem


class MaterialSystemBuilder:
    """Builder for systems with material handling (warehouse + AGVs)."""

    @staticmethod
    def build(
        env: Environment,
        *,
        n_servers: int = 6,
        n_agvs: int = 3,
        n_bays: int = 2,
        products: list[str] | None = None,
        initial_inventory: dict[str, int] | None = None,
        pick_time: float = 1.0,
        put_time: float = 0.5,
        travel_time: float = 2.0,
        collect_time_series: bool = False,
        retain_job_history: bool = False,
    ) -> MaterialSystem:
        """Build a complete material handling system.

        Args:
            env: The simulation environment.
            n_servers: Number of production servers.
            n_agvs: Number of AGV transport servers.
            n_bays: Number of warehouse bays (concurrent picks/puts).
            products: List of product names. Defaults to ["A", "B", "C"].
            initial_inventory: Initial stock levels. Defaults to 1000 each.
            pick_time: Time for warehouse pick operation.
            put_time: Time for warehouse put operation.
            travel_time: Time for AGV travel between locations.
            collect_time_series: Enable Server queue/utilization time-series tracking.
            retain_job_history: Retain processed job references on each Server.

        Returns:
            Tuple of (shopfloor, servers, warehouse, agvs, coordinator).
        """
        products = products or ["A", "B", "C"]
        initial_inventory = initial_inventory or {p: 1000 for p in products}

        shopfloor = ShopFloor(env=env)

        # Create production servers
        servers = tuple(
            Server(
                env=env,
                capacity=1,
                shopfloor=shopfloor,
                collect_time_series=collect_time_series,
                retain_job_history=retain_job_history,
            )
            for _ in range(n_servers)
        )

        # Create warehouse
        warehouse = Warehouse(
            env=env,
            n_bays=n_bays,
            products=products,
            initial_inventory=initial_inventory,
            pick_time_fn=lambda: pick_time,
            put_time_fn=lambda: put_time,
            shopfloor=shopfloor,
            collect_time_series=collect_time_series,
            retain_job_history=retain_job_history,
        )

        # Create AGVs
        agvs = tuple(
            AGV(
                env=env,
                travel_time_fn=lambda o, d: travel_time,
                shopfloor=shopfloor,
                agv_id=f"agv-{i}",
                collect_time_series=collect_time_series,
                retain_job_history=retain_job_history,
            )
            for i in range(n_agvs)
        )

        # Create coordinator
        coordinator = MaterialCoordinator(
            env=env,
            warehouse=warehouse,
            agvs=list(agvs),
            shopfloor=shopfloor,
        )

        # Wire coordinator to shopfloor for automatic material handling
        shopfloor.material_coordinator = coordinator

        return shopfloor, servers, warehouse, agvs, coordinator
