"""Builders for common jobshop system configurations."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from simulatte.distributions import server_sampling, truncated_2erlang
from simulatte.environment import Environment
from simulatte.policies.lumscor import LumsCor
from simulatte.policies.slar import Slar
from simulatte.policies.starvation_avoidance import starvation_avoidance_process
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import CorrectedWIPStrategy, ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.typing import PullSystem, PushSystem


def build_immediate_release_system(
    env: Environment,
    *,
    n_servers: int,
    arrival_rate: float = 1.0,
    service_rate: float = 1.0,
    collect_time_series: bool = False,
    retain_job_history: bool = False,
) -> PushSystem:
    """Build an immediate release (push) system with explicit env injection."""
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
        due_date_offset_distribution={"F1": lambda: random.expovariate(1.0 * n_servers)},
    )
    return None, servers, shop_floor, router


def build_lumscor_system(
    env: Environment,
    *,
    check_timeout: float,
    wl_norm_level: float,
    allowance_factor: int,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
) -> PullSystem:
    """Build a LumsCor (load-based) pull system.

    Args:
        env: The simulation environment.
        check_timeout: Time between pool release checks.
        wl_norm_level: Workload norm level for each server.
        allowance_factor: Allowance factor for due date calculation.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).

    Returns:
        Tuple of (psp, servers, shop_floor, router).
    """
    shop_floor = ShopFloor(env=env)
    shop_floor.set_wip_strategy(CorrectedWIPStrategy())
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))

    lumscor = LumsCor(wl_norm=dict.fromkeys(servers, float(wl_norm_level)), allowance_factor=int(allowance_factor))

    psp = PreShopPool(
        env=env,
        shopfloor=shop_floor,
        check_timeout=float(check_timeout),
        psp_release_policy=lumscor,
    )
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": server_sampling(servers)},
        sku_service_times={
            "F1": {
                server: lambda: truncated_2erlang(
                    lam=service_rate,
                    max_value=4.0,
                )
                for server in servers
            },
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
    )

    env.process(lumscor.starvation_trigger(shopfloor=shop_floor, psp=psp))
    env.process(starvation_avoidance_process(shop_floor, psp))  # type: ignore[arg-type]

    return psp, servers, shop_floor, router


def build_slar_system(
    env: Environment,
    *,
    allowance_factor: float,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
) -> PullSystem:
    """Build a SLAR (Superfluous Load Avoidance Release) pull system.

    Args:
        env: The simulation environment.
        allowance_factor: Slack allowance per operation (parameter 'k' in paper).
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).

    Returns:
        Tuple of (psp, servers, shop_floor, router).
    """
    shop_floor = ShopFloor(env=env)
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    psp = PreShopPool(env=env, shopfloor=shop_floor, check_timeout=0, psp_release_policy=None)
    slar = Slar(allowance_factor=allowance_factor)
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": server_sampling(servers)},
        sku_service_times={
            "F1": {
                server: lambda: truncated_2erlang(
                    lam=service_rate,
                    max_value=4.0,
                )
                for server in servers
            },
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
        priority_policies=lambda job, server: slar.pst_priority_policy(job, server) or 0.0,
    )
    env.process(slar.slar_release_triggers(shopfloor=shop_floor, psp=psp))
    env.process(starvation_avoidance_process(shop_floor, psp))  # type: ignore[arg-type]

    return psp, servers, shop_floor, router
