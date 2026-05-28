"""Builders for common jobshop system configurations."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from simulatte.dispatching_rules import planned_slack_time
from simulatte.distributions import server_sampling, truncated_2erlang
from simulatte.environment import Environment
from simulatte.policies.draco import Draco
from simulatte.policies.lumscor import LumsCor
from simulatte.policies.slar import Slar
from simulatte.policies.slar_limit import SlarLimit
from simulatte.policies.starvation_avoidance import starvation_avoidance
from simulatte.policies.triggers import on_completion_trigger, periodic_trigger
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import CorrectedWIPStrategy, CurrentWorkLoadCollector, ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import ProductionJob
    from simulatte.typing import PullSystem, PushSystem


def build_immediate_release_system(
    env: Environment,
    *,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_time_series: bool = False,
    retain_job_history: bool = False,
    priority_policies: Callable[[ProductionJob, Server], float] | None = None,
    collect_workload: bool = False,
) -> PushSystem:
    """Build an immediate release (push) system with no workload control.

    Creates a simple push system where jobs enter the shopfloor immediately
    upon arrival without any release control. Useful for baseline comparisons
    against pull systems (LumsCor, SLAR).

    Args:
        env: The simulation environment.
        n_servers: Number of production servers to create.
        arrival_rate: Inter-arrival rate (lambda for exponential distribution).
        service_rate: Service rate (lambda for truncated 2-Erlang distribution).
        collect_time_series: If True, servers collect queue length time series.
        retain_job_history: If True, servers retain completed job references.
        priority_policies: Optional callable used to assign job priorities at servers.

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
        for _ in range(n_servers)
    )

    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=None,
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
        priority_policies=priority_policies,
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
    collect_workload: bool = False,
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
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
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
        priority_policies=planned_slack_time(allowance=float(allowance_factor)),
    )

    # Compose release triggers
    env.process(periodic_trigger(psp, float(check_timeout), lumscor.periodic_release))
    shop_floor.on_processing_end(lambda job, server: lumscor.starvation_release(job, psp))
    psp.on_arrival(starvation_avoidance)

    return psp, servers, shop_floor, router


def build_slar_system(
    env: Environment,
    allowance_factor: float,
    *,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
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
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
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
    Slar(shopfloor=shop_floor, psp=psp, router=router, allowance_factor=allowance_factor)

    return psp, servers, shop_floor, router


def build_slar_limit_system(
    env: Environment,
    allowance_factor: float,
    *,
    wl_norm_level: float,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem:
    """Build a SLAR-Limit (load-bounded SLAR) pull system.

    Creates a pull system using SLAR-Limit release policy. SLAR-Limit
    extends classic SLAR by adding a workload-norm limit to the urgent
    insertion branch: urgent PSP candidates are iterated in ascending SPT
    order and the first whose corrected workload contribution PT/(i+1)
    fits all server norms is released. If none fits, the policy falls back
    to the standard SLAR postponed-starvation branch.

    Uses ``CorrectedWIPStrategy`` on the shopfloor (required by the norm check).

    Args:
        env: The simulation environment.
        allowance_factor: Slack allowance per operation (parameter 'k' in
            the SLAR paper).
        wl_norm_level: Workload norm threshold applied uniformly to every
            server. An urgent PSP candidate is released only if adding its
            corrected contribution keeps every server in its routing at or
            below this level.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential distribution).
        service_rate: Service rate (lambda for truncated 2-Erlang distribution).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector`` to
            the shopfloor for workload time-series.

    Returns:
        Tuple of (psp, servers, shop_floor, router).

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_slar_limit_system(
        ...     env, allowance_factor=3.0, wl_norm_level=5.0
        ... )
        >>> env.run(until=1000)

    References:
        Thürer, M. & Stevenson, M. (2021). Improving superfluous load
        avoidance release (SLAR): A new load-based SLAR mechanism.
        International Journal of Production Economics, 231, 107881.
        https://doi.org/10.1016/j.ijpe.2020.107881
    """
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
    shop_floor.set_wip_strategy(CorrectedWIPStrategy())
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
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
    SlarLimit(
        shopfloor=shop_floor,
        psp=psp,
        router=router,
        wl_norm=dict.fromkeys(servers, float(wl_norm_level)),
        allowance_factor=allowance_factor,
    )

    return psp, servers, shop_floor, router


def build_draco_system(
    env: Environment,
    *,
    wip_target: int,
    loop_target: int,
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    total_impact_weights: tuple[float, float, float] = (1.0 / 3, 1.0 / 3, 1.0 / 3),
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem:
    """Build a DRACO (non-hierarchical WIP control) pull system.

    Creates a pull system using the DRACO policy (Kasper, Land, Teunter
    2023, IJPE 257, 108768) that merges release, authorization, and
    dispatching into a single per-server decision. On every job
    completion at any server ``k``, DRACO scores every candidate in
    ``Q_k ∪ P_k`` by a weighted total impact ``w^R·R + w^A·A + w^D·D``
    and selects the maximum. The winner is dispatched to ``k`` next.

    Wiring:
        - ``priority_policy``: ``Draco.priority_policy`` (queue-side
          DRACO score; returns ``-inf`` one-shot for forced PSP winners
          to preserve strict paper semantics).
        - ``on_completion_trigger``: ``Draco.decide_next_job``.
        - ``psp.on_arrival(starvation_avoidance)``: prevents idle-server
          starvation when a new arrival's first server is idle.

    Args:
        env: The simulation environment.
        wip_target: Target shop WIP ``τ`` (count of jobs).
        loop_target: Target overlapping loop ``ε_{k,u}``. Scalar applied
            to every pair; for per-pair targets, instantiate ``Draco``
            directly with a ``dict[(Server, Server), int]``.
        focus_weights: FOCUS mechanism weights ``(w1, w2, w3, w4, w5)``.
        total_impact_weights: ``(w^R, w^A, w^D)`` for the DRACO total
            impact; must sum to 1.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(psp, servers, shop_floor, router)``.

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_draco_system(
        ...     env, wip_target=8, loop_target=4
        ... )
        >>> env.run(until=1000)

    References:
        Kasper, A., Land, M., Teunter, R. (2023). Non-hierarchical
        work-in-progress control in manufacturing. *International
        Journal of Production Economics*, 257, 108768.
    """
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    psp = PreShopPool(env=env, shopfloor=shop_floor)
    draco = Draco(
        shopfloor=shop_floor,
        psp=psp,
        focus_weights=focus_weights,
        total_impact_weights=total_impact_weights,
        wip_target=wip_target,
        loop_target=loop_target,
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
        priority_policies=lambda job, server: draco.priority_policy(job, server),
    )

    env.process(on_completion_trigger(shop_floor, psp, draco.decide_next_job))
    psp.on_arrival(starvation_avoidance)

    return psp, servers, shop_floor, router
