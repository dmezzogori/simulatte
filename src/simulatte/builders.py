"""Builders for common jobshop system configurations."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from simulatte.dispatching_rules import Focus, FocusPriorityRule
from simulatte.distributions import (
    arrival_rate_for_utilization,
    general_flow_shop_routing,
    pure_flow_shop_routing,
    pure_job_shop_routing,
    truncated_2erlang,
    twk_due_date,
)
from simulatte.environment import Environment
from simulatte.policies.continuous_release import ContinuousRelease
from simulatte.policies.conwip import ConWIP
from simulatte.policies.draco import Draco
from simulatte.policies.lumscor import LumsCor
from simulatte.policies.slar import Slar
from simulatte.policies.slar_limit import SlarLimit
from simulatte.policies.starvation_avoidance import starvation_avoidance
from simulatte.policies.triggers import on_completion_trigger
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import CorrectedWIPStrategy, CurrentWorkLoadCollector, ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

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
    due_date_offset_range: tuple[float, float] = (30.0, 45.0),
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
        due_date_offset_range: Range (low, high) for the uniform due-date offset
            added to each job's creation time. Tighter ranges make due dates bind,
            so due-date/tardiness dispatching rules differentiate.

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
        sku_routings={"F1": pure_job_shop_routing(servers)},
        sku_service_times={
            "F1": {
                server: lambda: truncated_2erlang(
                    lam=service_rate,
                    max_value=4.0,
                )
                for server in servers
            },
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(*due_date_offset_range)},  # noqa: S311
        priority_policies=priority_policies,
    )
    return None, servers, shop_floor, router


def build_focus_system(
    env: Environment,
    *,
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
    due_date_offset_range: tuple[float, float] = (30.0, 45.0),
) -> PushSystem:
    """Build an immediate-release (push) system that dispatches with FOCUS.

    Jobs enter the shopfloor on arrival (no release control); queue ordering
    at every server uses the FOCUS self-establishing rule (Kasper, Land,
    Teunter 2023, Omega 114, 102726) via ``FocusPriorityRule``. Use this to
    study FOCUS as a standalone dispatching rule, independent of DRACO.

    Args:
        env: The simulation environment.
        focus_weights: FOCUS mechanism weights ``(w1, w2, w3, w4, w5)`` for
            (pi, omega, psi, gamma, beta); must each be in ``[0, 1]`` and sum
            to 1. Defaults to beta-dormant ``(0.25, 0.25, 0.25, 0.25, 0.0)``.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.
        due_date_offset_range: Range (low, high) for the uniform due-date offset
            added to each job's creation time. Tighter ranges make due dates bind,
            so due-date/tardiness dispatching rules differentiate.

    Returns:
        Tuple of ``(None, servers, shop_floor, router)`` (push system; no PSP).

    Example:
        >>> env = Environment()
        >>> _, servers, shop_floor, router = build_focus_system(env)
        >>> env.run(until=1000)

    References:
        Kasper, A., Land, M., Teunter, R. (2023). Towards system state
        dispatching in high-variety manufacturing. *Omega*, 114, 102726.
    """
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    focus = Focus(weights=focus_weights)
    priority = FocusPriorityRule(focus, shop_floor)
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=None,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": pure_job_shop_routing(servers)},
        sku_service_times={
            "F1": {
                server: lambda: truncated_2erlang(
                    lam=service_rate,
                    max_value=4.0,
                )
                for server in servers
            },
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(*due_date_offset_range)},  # noqa: S311
        priority_policies=priority,
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
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    psp = PreShopPool(env=env, shopfloor=shop_floor)
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": pure_job_shop_routing(servers)},
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
    # LumsCor self-wires CorrectedWIPStrategy, router.priority_policies (PST),
    # the periodic release trigger, shop_floor.on_processing_end, and
    # psp.on_arrival(starvation_avoidance); constructed for those effects.
    LumsCor(
        shopfloor=shop_floor,
        psp=psp,
        router=router,
        wl_norm=float(wl_norm_level),
        check_timeout=float(check_timeout),
        allowance_factor=int(allowance_factor),
    )

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
        sku_routings={"F1": pure_job_shop_routing(servers)},
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
        sku_routings={"F1": pure_job_shop_routing(servers)},
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
    total_impact_weights: tuple[float, float, float] = (0.25, 0.25, 0.5),
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

    Wiring (performed by ``Draco.__init__``):
        - ``priority_policy``: ``Draco.priority_policy`` (queue-side
          DRACO score; returns ``-inf`` one-shot for forced PSP winners
          to preserve strict paper semantics).
        - ``shop_floor.on_processing_end``: ``Draco.decide_next_job``.
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
            impact; must sum to 1. Defaults to ``(0.25, 0.25, 0.5)`` — the
            paper's full DRACO configuration (Table 2).
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
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": pure_job_shop_routing(servers)},
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
    # Draco self-wires router.priority_policies, shop_floor.on_processing_end,
    # and psp.on_arrival(starvation_avoidance); constructed for those effects.
    Draco(
        shopfloor=shop_floor,
        router=router,
        psp=psp,
        focus_weights=focus_weights,
        total_impact_weights=total_impact_weights,
        wip_target=wip_target,
        loop_target=loop_target,
    )

    return psp, servers, shop_floor, router


def build_conwip_system(
    env: Environment,
    *,
    wip_cap: int,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem:
    """Build a ConWIP (Constant Work-In-Process) pull system.

    Jobs wait in the Pre-Shop Pool and are released — earliest due date
    first — only while the shop holds fewer than ``wip_cap`` jobs. Release
    is re-checked on every job completion and on every PSP arrival.

    Args:
        env: The simulation environment.
        wip_cap: Maximum number of jobs allowed on the shop floor at once.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(psp, servers, shop_floor, router)``.

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_conwip_system(env, wip_cap=8)
        >>> env.run(until=1000)

    References:
        Spearman, M. L., Woodruff, D. L. & Hopp, W. J. (1990). CONWIP: a pull
        alternative to kanban. International Journal of Production Research,
        28(5), 879-894.
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
        sku_routings={"F1": pure_job_shop_routing(servers)},
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
    ConWIP(shopfloor=shop_floor, psp=psp, wip_cap=wip_cap)

    return psp, servers, shop_floor, router


def build_continuous_release_system(
    env: Environment,
    *,
    wl_norm_level: float,
    allowance_factor: int = 2,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem:
    """Build a Continuous Release (workload-controlled) pull system.

    Jobs are held in the Pre-Shop Pool and released continuously — on each
    job completion and on PSP arrival — when their corrected workload
    contribution keeps every server in their routing at or below
    ``wl_norm_level``. Requires ``CorrectedWIPStrategy`` on the shopfloor.

    Args:
        env: The simulation environment.
        wl_norm_level: Corrected workload norm applied uniformly to every server.
        allowance_factor: Buffer time per server for due-date planning.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(psp, servers, shop_floor, router)``.

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_continuous_release_system(
        ...     env, wl_norm_level=6.0
        ... )
        >>> env.run(until=1000)

    References:
        Fernandes, N. O. & Carmo-Silva, S. (2011). Workload control under
        continuous order release. International Journal of Production
        Economics, 131(1), 257-262.
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
        sku_routings={"F1": pure_job_shop_routing(servers)},
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
    cr = ContinuousRelease(
        wl_norm=dict.fromkeys(servers, float(wl_norm_level)),
        allowance_factor=int(allowance_factor),
    )
    env.process(on_completion_trigger(shop_floor, psp, cr.on_completion_release))
    psp.on_arrival(cr.on_arrival_release)

    return psp, servers, shop_floor, router


def build_starvation_avoidance_system(
    env: Environment,
    *,
    n_servers: int = 6,
    arrival_rate: float = 1 / 0.648,
    service_rate: float = 2.0,
    collect_workload: bool = False,
) -> PullSystem:
    """Build a starvation-avoidance-only pull system.

    The simplest pull policy: a job is released from the Pre-Shop Pool only
    when its first routing server is idle — checked on PSP arrival and again
    on every job completion. There is no workload norm or WIP cap; release is
    driven purely by first-server starvation.

    Args:
        env: The simulation environment.
        n_servers: Number of production servers.
        arrival_rate: Inter-arrival rate (lambda for exponential).
        service_rate: Service rate (lambda for truncated 2-Erlang).
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(psp, servers, shop_floor, router)``.

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_starvation_avoidance_system(env)
        >>> env.run(until=1000)
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
        sku_routings={"F1": pure_job_shop_routing(servers)},
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

    def _release_idle_first_server(_triggering_job: ProductionJob, _server: Server) -> None:
        for job in list(psp.jobs):
            if job.servers[0].is_idle:
                psp.release(job)

    psp.on_arrival(starvation_avoidance)
    shop_floor.on_processing_end(_release_idle_first_server)

    return psp, servers, shop_floor, router


def _build_benchmark_shop(
    env: Environment,
    *,
    routing_factory: Callable[[Sequence[Server]], Callable[[], Sequence[Server]]],
    mean_routing_length: float,
    n_servers: int,
    target_utilization: float,
    service_rate: float,
    due_date_offset_range: tuple[float, float],
    twk_allowance_factor: float | None,
    collect_workload: bool,
) -> PushSystem:
    """Wire an immediate-release (push) benchmark shop for a given routing factory.

    Shared core of the preconfigured PPC benchmark shops. Builds ``n_servers``
    single-capacity servers, derives the exponential arrival rate from the target
    utilization and the shop's mean routing length
    (``rho = lambda * E[L] * E[p] / M``), and assembles a push ``Router`` whose
    routing structure is supplied by ``routing_factory``. Optionally applies a
    Total Work Content (TWK) due-date rule in place of the flat uniform allowance.
    """
    shop_floor = ShopFloor(
        env=env,
        time_series_collector=CurrentWorkLoadCollector() if collect_workload else None,
    )
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    arrival_rate = arrival_rate_for_utilization(
        target_utilization,
        n_servers=n_servers,
        mean_routing_length=mean_routing_length,
        mean_processing_time=2.0 / service_rate,
    )
    due_date_rule = {"F1": twk_due_date(twk_allowance_factor)} if twk_allowance_factor is not None else None
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=None,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        sku_distributions={"F1": 1},
        sku_routings={"F1": routing_factory(servers)},
        sku_service_times={
            "F1": {
                server: lambda: truncated_2erlang(
                    lam=service_rate,
                    max_value=4.0,
                )
                for server in servers
            },
        },
        due_date_offset_distribution={"F1": lambda: random.uniform(*due_date_offset_range)},  # noqa: S311
        due_date_rule=due_date_rule,
    )
    return None, servers, shop_floor, router


def build_pure_job_shop_system(
    env: Environment,
    *,
    n_servers: int = 6,
    target_utilization: float = 0.90,
    service_rate: float = 2.0,
    due_date_offset_range: tuple[float, float] = (30.0, 45.0),
    twk_allowance_factor: float | None = None,
    collect_workload: bool = False,
) -> PushSystem:
    """Build a Pure Job Shop (PJS) benchmark environment.

    The Pure Job Shop — a *randomly routed job shop* — is the least directed
    standard benchmark: each order has a random routing length ``U[1, M]`` and a
    random routing direction (undirected), visiting each machine at most once
    (no re-entry). Jobs enter the shopfloor immediately on arrival (push /
    uncontrolled release), the canonical baseline against which release and
    dispatching strategies are compared.

    The arrival rate is **derived** from ``target_utilization`` and the shop's
    mean routing length ``E[L] = (M + 1) / 2`` so the shop runs at the requested
    utilization regardless of ``n_servers`` (at the defaults this reproduces the
    literature's mean inter-arrival time of 0.648).

    Args:
        env: The simulation environment.
        n_servers: Number of single-capacity work centres ``M``.
        target_utilization: Target steady-state utilization ``rho`` in (0, 1].
        service_rate: Rate ``lambda`` of the truncated 2-Erlang processing-time
            distribution (mean ``2 / service_rate``).
        due_date_offset_range: Range ``(low, high)`` for the uniform due-date
            allowance added to each job's arrival time. Ignored when
            ``twk_allowance_factor`` is set.
        twk_allowance_factor: If set, use a Total Work Content due-date rule
            ``due_date = arrival + K * sum(p_ij)`` with ``K = twk_allowance_factor``
            instead of the flat uniform allowance.
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(None, servers, shop_floor, router)`` (push system; no PSP).

    Example:
        >>> env = Environment()
        >>> _, servers, shop_floor, router = build_pure_job_shop_system(env)
        >>> env.run(until=1000)

    References:
        Kasper, A., Land, M., & Teunter, R. (2023). Towards system state
        dispatching in high-variety manufacturing. *Omega*, 114, 102726.
        https://doi.org/10.1016/j.omega.2022.102726
    """
    return _build_benchmark_shop(
        env,
        routing_factory=pure_job_shop_routing,
        mean_routing_length=(n_servers + 1) / 2,
        n_servers=n_servers,
        target_utilization=target_utilization,
        service_rate=service_rate,
        due_date_offset_range=due_date_offset_range,
        twk_allowance_factor=twk_allowance_factor,
        collect_workload=collect_workload,
    )


def build_general_flow_shop_system(
    env: Environment,
    *,
    n_servers: int = 6,
    target_utilization: float = 0.90,
    service_rate: float = 2.0,
    due_date_offset_range: tuple[float, float] = (30.0, 45.0),
    twk_allowance_factor: float | None = None,
    collect_workload: bool = False,
) -> PushSystem:
    """Build a General Flow Shop (GFS) benchmark environment.

    The General Flow Shop is the *directed* counterpart of the Pure Job Shop:
    each order has the same random routing length ``U[1, M]`` and equal
    per-machine inclusion probability, but the selected machines are sorted into
    ascending index order so orders flow in a single direction with typical
    upstream and downstream stations. No re-entry. Jobs enter the shopfloor
    immediately on arrival (push / uncontrolled release).

    The arrival rate is **derived** from ``target_utilization`` and the shop's
    mean routing length ``E[L] = (M + 1) / 2`` (identical to the Pure Job Shop;
    only the routing *direction* differs), reproducing the literature's mean
    inter-arrival time of 0.648 at the defaults.

    Args:
        env: The simulation environment.
        n_servers: Number of single-capacity work centres ``M``.
        target_utilization: Target steady-state utilization ``rho`` in (0, 1].
        service_rate: Rate ``lambda`` of the truncated 2-Erlang processing-time
            distribution (mean ``2 / service_rate``).
        due_date_offset_range: Range ``(low, high)`` for the uniform due-date
            allowance. Ignored when ``twk_allowance_factor`` is set.
        twk_allowance_factor: If set, use a Total Work Content due-date rule
            (``due_date = arrival + K * sum(p_ij)``) instead of the flat allowance.
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(None, servers, shop_floor, router)`` (push system; no PSP).

    Example:
        >>> env = Environment()
        >>> _, servers, shop_floor, router = build_general_flow_shop_system(env)
        >>> env.run(until=1000)

    References:
        Oosterman, B., Land, M., & Gaalman, G. (2000). The influence of shop
        characteristics on workload control. *International Journal of
        Production Economics*, 68(1), 107-119.
        https://doi.org/10.1016/S0925-5273(99)00141-3
    """
    return _build_benchmark_shop(
        env,
        routing_factory=general_flow_shop_routing,
        mean_routing_length=(n_servers + 1) / 2,
        n_servers=n_servers,
        target_utilization=target_utilization,
        service_rate=service_rate,
        due_date_offset_range=due_date_offset_range,
        twk_allowance_factor=twk_allowance_factor,
        collect_workload=collect_workload,
    )


def build_pure_flow_shop_system(
    env: Environment,
    *,
    n_servers: int = 6,
    target_utilization: float = 0.90,
    service_rate: float = 2.0,
    due_date_offset_range: tuple[float, float] = (30.0, 45.0),
    twk_allowance_factor: float | None = None,
    collect_workload: bool = False,
) -> PushSystem:
    """Build a Pure Flow Shop (PFS) benchmark environment.

    The Pure Flow Shop is the most directed benchmark: every order has a fixed
    routing length equal to the number of machines and visits *all* servers in
    the same fixed (directed) sequence. Jobs enter the shopfloor immediately on
    arrival (push / uncontrolled release).

    Because every order visits every machine, the mean routing length is
    ``E[L] = M`` rather than ``(M + 1) / 2``. The arrival rate is **derived**
    accordingly, reproducing the literature's mean inter-arrival time of 1.111 at
    the defaults. (Reusing the job-shop rate of 1/0.648 here would drive
    ``rho ~ 1.54`` and the shop unstable — the derivation avoids that trap.)

    Args:
        env: The simulation environment.
        n_servers: Number of single-capacity work centres ``M``.
        target_utilization: Target steady-state utilization ``rho`` in (0, 1].
        service_rate: Rate ``lambda`` of the truncated 2-Erlang processing-time
            distribution (mean ``2 / service_rate``).
        due_date_offset_range: Range ``(low, high)`` for the uniform due-date
            allowance. Ignored when ``twk_allowance_factor`` is set.
        twk_allowance_factor: If set, use a Total Work Content due-date rule
            (``due_date = arrival + K * sum(p_ij)``) instead of the flat allowance.
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(None, servers, shop_floor, router)`` (push system; no PSP).

    Example:
        >>> env = Environment()
        >>> _, servers, shop_floor, router = build_pure_flow_shop_system(env)
        >>> env.run(until=1000)

    References:
        Kasper, A., Land, M., & Teunter, R. (2023). Towards system state
        dispatching in high-variety manufacturing. *Omega*, 114, 102726.
        https://doi.org/10.1016/j.omega.2022.102726
    """
    return _build_benchmark_shop(
        env,
        routing_factory=pure_flow_shop_routing,
        mean_routing_length=n_servers,
        n_servers=n_servers,
        target_utilization=target_utilization,
        service_rate=service_rate,
        due_date_offset_range=due_date_offset_range,
        twk_allowance_factor=twk_allowance_factor,
        collect_workload=collect_workload,
    )
