"""Builders for common jobshop system configurations."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from simulatte.distributions import server_sampling, truncated_2erlang
from simulatte.environment import Environment
from simulatte.policies.lumscor import LumsCor
from simulatte.policies.slar import Slar
from simulatte.policies.starvation_avoidance import starvation_avoidance_process
from simulatte.policies.triggers import on_completion_trigger, periodic_trigger
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
    """Build an immediate release (push) system with no workload control.

    Creates a simple push system where jobs enter the shopfloor immediately
    upon arrival without any release control. Useful for baseline comparisons
    against pull systems (LumsCor, SLAR).

    Args:
        env: The simulation environment.
        n_servers: Number of production servers to create.
        arrival_rate: Inter-arrival rate (lambda for exponential distribution).
        service_rate: Service rate (lambda for exponential distribution).
        collect_time_series: If True, servers collect queue length time series.
        retain_job_history: If True, servers retain completed job references.

    Returns:
        Tuple of (psp, servers, shop_floor, router) where psp is None.

    Example:
        >>> env = Environment()
        >>> _, servers, shop_floor, router = build_immediate_release_system(
        ...     env, n_servers=6, arrival_rate=1.5
        ... )
        >>> env.run(until=1000)
        >>> print(f"Jobs completed: {len(shop_floor.jobs_done)}")
    """
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
    """Build a LumsCor (load-based) pull system with workload control.

    Creates a pull system using LUMS-COR (Land's Upper limit for Make-Span
    with CORrected workload) release policy. Jobs are held in a Pre-Shop Pool
    and released only when server workloads stay below configured norms.

    Uses CorrectedWIPStrategy which discounts downstream workload by position,
    and includes starvation avoidance triggers for idle servers.

    Args:
        env: The simulation environment.
        check_timeout: Time between pool release checks.
        wl_norm_level: Workload norm threshold for each server. Jobs are
            released only if adding them keeps corrected WIP at or below this level.
        allowance_factor: Buffer time per server for due date calculation.
            Higher values result in earlier (more conservative) releases.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential distribution).
        service_rate: Service rate (lambda for truncated 2-Erlang distribution).

    Returns:
        Tuple of (psp, servers, shop_floor, router).

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_lumscor_system(
        ...     env, check_timeout=10.0, wl_norm_level=5.0, allowance_factor=2
        ... )
        >>> env.run(until=1000)

    References:
        Land, M.J. (2006). Parameters and sensitivity in workload control.
        International Journal of Production Economics, 104(2), 625-638.
    """
    shop_floor = ShopFloor(env=env)
    shop_floor.set_wip_strategy(CorrectedWIPStrategy())
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))

    lumscor = LumsCor(wl_norm=dict.fromkeys(servers, float(wl_norm_level)), allowance_factor=int(allowance_factor))
    psp = PreShopPool(env=env, shopfloor=shop_floor)
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

    # Compose release triggers
    env.process(periodic_trigger(psp, float(check_timeout), lumscor.periodic_release))
    env.process(on_completion_trigger(shop_floor, psp, lumscor.starvation_release))
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

    Creates a pull system using SLAR release policy based on planned slack
    times (PST). Jobs are released from the Pre-Shop Pool when servers risk
    starvation or when urgent jobs need insertion.

    Release triggers:
        - Starvation avoidance: When queue is empty or has one job, release
          the job with earliest planned start time.
        - Urgent job insertion: When all queued jobs are non-urgent, insert
          the most urgent job with shortest processing time.

    Args:
        env: The simulation environment.
        allowance_factor: Slack allowance per operation (parameter 'k' in paper).
            Higher values provide more buffer time per server.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential distribution).
        service_rate: Service rate (lambda for truncated 2-Erlang distribution).

    Returns:
        Tuple of (psp, servers, shop_floor, router).

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_slar_system(
        ...     env, allowance_factor=3.0
        ... )
        >>> env.run(until=1000)

    References:
        Land, M.J. & Gaalman, G.J.C. (1998). The performance of workload control
        concepts in job shops: Improving the release method.
        International Journal of Production Economics, 56-57, 347-364.
        https://doi.org/10.1016/S0925-5273(98)00052-8
    """
    shop_floor = ShopFloor(env=env)
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    slar = Slar(allowance_factor=allowance_factor)
    psp = PreShopPool(env=env, shopfloor=shop_floor)
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

    # Compose release triggers (event-driven only, no periodic)
    env.process(on_completion_trigger(shop_floor, psp, slar.starvation_release))
    env.process(starvation_avoidance_process(shop_floor, psp))  # type: ignore[arg-type]

    return psp, servers, shop_floor, router
