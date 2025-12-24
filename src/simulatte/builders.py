"""Builders for common jobshop system configurations."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Literal

from simulatte.agv import AGV
from simulatte.distributions import server_sampling, truncated_2erlang
from simulatte.environment import Environment
from simulatte.materials import MaterialCoordinator
from simulatte.policies.lumscor import LumsCor, lumscor_starvation_trigger
from simulatte.policies.slar import Slar
from simulatte.policies.starvation_avoidance import starvation_avoidance_process
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.warehouse_store import WarehouseStore

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.typing import PushSystem, System

# Type alias for material system
type MaterialSystem = tuple[
    ShopFloor,
    tuple[Server, ...],
    WarehouseStore,
    tuple[AGV, ...],
    MaterialCoordinator,
]


def build_push_system(
    env: Environment,
    n_servers: int,
    arrival_rate: float = 1.0,
    service_rate: float = 1.0,
    *,
    collect_time_series: bool = False,
    retain_job_history: bool = False,
) -> PushSystem:
    """Build a simple push system with explicit env injection."""
    shop_floor = ShopFloor(env=env)
    servers = tuple(
        Server(
            env=env,
            capacity=1,
            shopfloor=shop_floor,
            collect_time_series=collect_time_series,
            retain_job_history=retain_job_history,
        )
        for _ in range(n_servers)
    )
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=None,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": lambda: servers},
        sku_service_times={
            "F1": {server: lambda: random.expovariate(service_rate) for server in servers},
        },
        waiting_time_distribution={"F1": lambda: random.expovariate(1.0 * n_servers)},
    )
    return None, servers, shop_floor, router


class LiteratureSystemBuilder:
    """Builder with literature-inspired defaults."""

    _n_servers: int = 6
    _arrival_rate: float = 1 / 0.648
    _service_rate: float = 2.0

    @staticmethod
    def __call__(
        env: Environment,
        system: Literal["push", "lumscor", "slar"],
        *,
        check_timeout: float | int | None = None,
        wl_norm_level: float | int | None = None,
        allowance_factor: float | int | None = None,
    ) -> System:
        if system == "push":
            return LiteratureSystemBuilder.build_system_push(env)
        if system == "lumscor":
            assert check_timeout is not None and wl_norm_level is not None and allowance_factor is not None
            return LiteratureSystemBuilder.build_system_lumscor(
                env,
                check_timeout=float(check_timeout),
                wl_norm_level=float(wl_norm_level),
                allowance_factor=float(allowance_factor),
            )
        if system == "slar":
            assert allowance_factor is not None
            return LiteratureSystemBuilder.build_system_slar(
                env,
                allowance_factor=float(allowance_factor),
            )
        msg = f"Unknown system type: {system}"
        raise ValueError(msg)

    @staticmethod
    def build_system_push(env: Environment) -> System:
        shop_floor = ShopFloor(env=env)
        servers = tuple(
            Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(LiteratureSystemBuilder._n_servers)
        )
        router = Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=None,
            inter_arrival_distribution=lambda: random.expovariate(LiteratureSystemBuilder._arrival_rate),
            sku_distributions={"F1": 1},
            sku_routings={"F1": server_sampling(servers)},
            sku_service_times={
                "F1": {
                    server: lambda: truncated_2erlang(
                        lam=LiteratureSystemBuilder._service_rate,
                        max_value=4.0,
                    )
                    for server in servers
                },
            },
            waiting_time_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
        )
        return None, servers, shop_floor, router

    @staticmethod
    def build_system_lumscor(
        env: Environment,
        check_timeout: float | int,
        wl_norm_level: float | int,
        allowance_factor: float | int,
    ) -> System:
        shop_floor = ShopFloor(env=env)
        servers = tuple(
            Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(LiteratureSystemBuilder._n_servers)
        )
        psp = PreShopPool(
            env=env,
            shopfloor=shop_floor,
            check_timeout=float(check_timeout),
            psp_release_policy=LumsCor(
                shopfloor=shop_floor,
                wl_norm=dict.fromkeys(servers, float(wl_norm_level)),
                allowance_factor=int(allowance_factor),
            ),
        )
        router = Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=psp,
            inter_arrival_distribution=lambda: random.expovariate(LiteratureSystemBuilder._arrival_rate),
            sku_distributions={"F1": 1},
            sku_routings={"F1": server_sampling(servers)},
            sku_service_times={
                "F1": {
                    server: lambda: truncated_2erlang(
                        lam=LiteratureSystemBuilder._service_rate,
                        max_value=4.0,
                    )
                    for server in servers
                },
            },
            waiting_time_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
        )

        env.process(lumscor_starvation_trigger(shopfloor=shop_floor, psp=psp))
        env.process(starvation_avoidance_process(shop_floor, psp))  # type: ignore[arg-type]

        return psp, servers, shop_floor, router

    @staticmethod
    def build_system_slar(env: Environment, allowance_factor: float | int) -> System:
        shop_floor = ShopFloor(env=env)
        servers = tuple(
            Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(LiteratureSystemBuilder._n_servers)
        )
        psp = PreShopPool(env=env, shopfloor=shop_floor, check_timeout=0, psp_release_policy=None)
        slar = Slar(allowance_factor=int(allowance_factor))
        router = Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=psp,
            inter_arrival_distribution=lambda: random.expovariate(LiteratureSystemBuilder._arrival_rate),
            sku_distributions={"F1": 1},
            sku_routings={"F1": server_sampling(servers)},
            sku_service_times={
                "F1": {
                    server: lambda: truncated_2erlang(
                        lam=LiteratureSystemBuilder._service_rate,
                        max_value=4.0,
                    )
                    for server in servers
                },
            },
            waiting_time_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
            priority_policies=lambda job, server: slar.pst_priority_policy(job, server) or 0.0,
        )
        env.process(slar.slar_release_triggers(shopfloor=shop_floor, psp=psp))
        env.process(starvation_avoidance_process(shop_floor, psp))  # type: ignore[arg-type]

        return psp, servers, shop_floor, router


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
        warehouse = WarehouseStore(
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
