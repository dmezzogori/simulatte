"""Builders for common jobshop system configurations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.dispatching_rules import Focus, FocusPriorityRule
from simulatte.environment import Environment
from simulatte.policies.continuous_release import ContinuousRelease
from simulatte.policies.conwip import ConWIP
from simulatte.policies.draco import Draco
from simulatte.policies.lumscor import LumsCor
from simulatte.policies.slar import Slar
from simulatte.policies.slar_limit import SlarLimit
from simulatte.policies.starvation_avoidance import starvation_avoidance
from simulatte.psp import PreShopPool
from simulatte.scenario import Scenario
from simulatte.server import Server

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import ProductionJob
    from simulatte.typing import PullSystem, PushSystem


def build_immediate_release_system(
    env: Environment,
    *,
    scenario: Scenario = Scenario(),
    priority_policies: Callable[..., float] | None = None,
    collect_workload: bool = False,
    collect_time_series: bool = False,
    retain_job_history: bool = False,
) -> PushSystem:
    """Build an immediate release (push) system with no workload control.

    Creates a simple push system where jobs enter the shopfloor immediately
    upon arrival without any release control. Useful for baseline comparisons
    against pull systems (LumsCor, SLAR).

    Args:
        env: The simulation environment.
        scenario: Environment description (shop type, machine count, arrival
            process, service-time, due-date rule). Defaults to a 6-machine
            pure job shop at rho=0.90.
        priority_policies: Optional callable used to assign job priorities at servers.
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.
        collect_time_series: If True, servers collect queue length time series.
        retain_job_history: If True, servers retain completed job references.

    Returns:
        Tuple of (psp, servers, shop_floor, router) where psp is None.

    Example:
        >>> env = Environment()
        >>> _, servers, shop_floor, router = build_immediate_release_system(env)
        >>> env.run(until=1000)
        >>> print(f"Jobs completed: {len(shop_floor.jobs_done)}")
    """
    sf, servers = scenario.build_floor(
        env,
        collect_workload=collect_workload,
        collect_time_series=collect_time_series,
        retain_job_history=retain_job_history,
    )
    router = scenario.build_router(env, sf, servers, psp=None, priority_policies=priority_policies)
    return None, servers, sf, router


def build_focus_system(
    env: Environment,
    *,
    scenario: Scenario = Scenario(),
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    collect_workload: bool = False,
) -> PushSystem:
    """Build an immediate-release (push) system that dispatches with FOCUS.

    Jobs enter the shopfloor on arrival (no release control); queue ordering
    at every server uses the FOCUS self-establishing rule (Kasper, Land,
    Teunter 2023, Omega 114, 102726) via ``FocusPriorityRule``. Use this to
    study FOCUS as a standalone dispatching rule, independent of DRACO.

    Args:
        env: The simulation environment.
        scenario: Environment description (shop type, machine count, arrival
            process, service-time, due-date rule). Defaults to a 6-machine
            pure job shop at rho=0.90.
        focus_weights: FOCUS mechanism weights ``(w1, w2, w3, w4, w5)`` for
            (pi, omega, psi, gamma, beta); must each be in ``[0, 1]`` and sum
            to 1. Defaults to beta-dormant ``(0.25, 0.25, 0.25, 0.25, 0.0)``.
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

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
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    priority = FocusPriorityRule(Focus(weights=focus_weights), sf)
    router = scenario.build_router(env, sf, servers, psp=None, priority_policies=priority)
    return None, servers, sf, router


def build_lumscor_system(
    env: Environment,
    *,
    scenario: Scenario = Scenario(),
    check_timeout: float,
    wl_norm_level: float,
    allowance_factor: int,
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
        scenario: Environment description (shop type, machine count, arrival
            process, service-time, due-date rule). Defaults to a 6-machine
            pure job shop at rho=0.90.
        check_timeout: Time between pool release checks.
        wl_norm_level: Workload norm threshold for each server. Jobs are
            released only if adding them keeps corrected WIP at or below this level.
        allowance_factor: Buffer time per server for due date calculation.
            Higher values result in earlier (more conservative) releases.
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

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
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    # LumsCor self-wires CorrectedWIPStrategy, router.priority_policies (PST),
    # the periodic release trigger, shop_floor.on_processing_end, and
    # psp.on_arrival(starvation_avoidance); constructed for those effects.
    LumsCor(
        shopfloor=sf,
        psp=psp,
        router=router,
        wl_norm=wl_norm_level,
        check_timeout=check_timeout,
        allowance_factor=allowance_factor,
    )
    return psp, servers, sf, router


def build_slar_system(
    env: Environment,
    allowance_factor: float,
    *,
    scenario: Scenario = Scenario(),
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
        scenario: Environment description (shop type, machine count, arrival
            process, service-time, due-date rule). Defaults to a 6-machine
            pure job shop at rho=0.90.
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

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
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    Slar(shopfloor=sf, psp=psp, router=router, allowance_factor=allowance_factor)
    return psp, servers, sf, router


def build_slar_limit_system(
    env: Environment,
    allowance_factor: float,
    *,
    wl_norm_level: float,
    scenario: Scenario = Scenario(),
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
        scenario: Environment description (shop type, machine count, arrival
            process, service-time, due-date rule). Defaults to a 6-machine
            pure job shop at rho=0.90.
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
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    # SlarLimit self-wires CorrectedWIPStrategy, router.priority_policies (PST),
    # shop_floor.on_processing_end, and psp.on_arrival(starvation_avoidance);
    # constructed for those effects.
    SlarLimit(shopfloor=sf, psp=psp, router=router, wl_norm=wl_norm_level, allowance_factor=allowance_factor)
    return psp, servers, sf, router


def build_draco_system(
    env: Environment,
    *,
    wip_target: int,
    loop_target: int,
    focus_weights: tuple[float, float, float, float, float] = (0.25, 0.25, 0.25, 0.25, 0.0),
    total_impact_weights: tuple[float, float, float] = (0.25, 0.25, 0.5),
    scenario: Scenario = Scenario(),
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
        scenario: Environment description (shop type, machine count, arrival
            process, service-time, due-date rule). Defaults to a 6-machine
            pure job shop at rho=0.90.
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
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    # Draco self-wires router.priority_policies, shop_floor.on_processing_end,
    # and psp.on_arrival(starvation_avoidance); constructed for those effects.
    Draco(
        shopfloor=sf,
        router=router,
        psp=psp,
        focus_weights=focus_weights,
        total_impact_weights=total_impact_weights,
        wip_target=wip_target,
        loop_target=loop_target,
    )
    return psp, servers, sf, router


def build_conwip_system(
    env: Environment,
    *,
    wip_cap: int,
    scenario: Scenario = Scenario(),
    collect_workload: bool = False,
) -> PullSystem:
    """Build a ConWIP (Constant Work-In-Process) pull system.

    Jobs wait in the Pre-Shop Pool and are released — earliest due date
    first — only while the shop holds fewer than ``wip_cap`` jobs. Release
    is re-checked on every job completion and on every PSP arrival.

    Args:
        env: The simulation environment.
        wip_cap: Maximum number of jobs allowed on the shop floor at once.
        scenario: Environment description (shop type, machine count, arrival
            process, service-time, due-date rule). Defaults to a 6-machine
            pure job shop at rho=0.90.
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
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    ConWIP(shopfloor=sf, psp=psp, wip_cap=wip_cap)
    return psp, servers, sf, router


def build_continuous_release_system(
    env: Environment,
    *,
    wl_norm_level: float,
    allowance_factor: int = 2,
    scenario: Scenario = Scenario(),
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
        scenario: Environment description (shop type, machine count, arrival
            process, service-time, due-date rule). Defaults to a 6-machine
            pure job shop at rho=0.90.
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
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)
    # ContinuousRelease self-wires CorrectedWIPStrategy, the completion-triggered
    # release, and psp.on_arrival; constructed for those effects.
    ContinuousRelease(shopfloor=sf, psp=psp, wl_norm=wl_norm_level, allowance_factor=allowance_factor)
    return psp, servers, sf, router


def build_starvation_avoidance_system(
    env: Environment,
    *,
    scenario: Scenario = Scenario(),
    collect_workload: bool = False,
) -> PullSystem:
    """Build a starvation-avoidance-only pull system.

    The simplest pull policy: a job is released from the Pre-Shop Pool only
    when its first routing server is idle — checked on PSP arrival and again
    on every job completion. There is no workload norm or WIP cap; release is
    driven purely by first-server starvation.

    Args:
        env: The simulation environment.
        scenario: Environment description (shop type, machine count, arrival
            process, service-time, due-date rule). Defaults to a 6-machine
            pure job shop at rho=0.90.
        collect_workload: If True, attach a ``CurrentWorkLoadCollector``.

    Returns:
        Tuple of ``(psp, servers, shop_floor, router)``.

    Example:
        >>> env = Environment()
        >>> psp, servers, shop_floor, router = build_starvation_avoidance_system(env)
        >>> env.run(until=1000)
    """
    sf, servers = scenario.build_floor(env, collect_workload=collect_workload)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = scenario.build_router(env, sf, servers, psp=psp)

    def _release_idle_first_server(_triggering_job: ProductionJob, _server: Server) -> None:
        for job in list(psp.jobs):
            if job.servers[0].is_idle:
                psp.release(job)

    psp.on_arrival(starvation_avoidance)
    sf.on_processing_end(_release_idle_first_server)
    return psp, servers, sf, router
