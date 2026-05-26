"""Tests for the FOCUS dispatching rule and its adapter / refresh helper."""

from __future__ import annotations

import math

import pytest

from simulatte.dispatching_rules import (
    Focus,
    FocusContext,
    FocusPriorityRule,
    refresh_focus_queue,
)
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


# ----- Init validation -----


def test_focus_init_validates_weights_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        Focus(weights=(0.5, 0.5, 0.5, 0.5))


def test_focus_init_validates_weights_count() -> None:
    with pytest.raises(ValueError, match="4 elements"):
        Focus(weights=(0.5, 0.5))  # type: ignore[arg-type]


def test_focus_init_validates_weights_range() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        Focus(weights=(-0.1, 0.5, 0.3, 0.3))


def test_focus_init_accepts_default_equal_weights() -> None:
    focus = Focus()
    assert focus.w1 == focus.w2 == focus.w3 == focus.w4 == 0.25


# ----- FocusContext aggregates -----


def test_focus_context_max_pij_basic() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    j1 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 7.0], due_date=100.0)
    j2 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[5.0, 2.0], due_date=100.0)
    sf.add(j1)
    sf.add(j2)

    ctx = Focus.build_context(sf, now=0.0)
    assert ctx.max_pij == 7.0


def test_focus_context_max_pij_zero_when_no_jobs() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    Server(env=env, capacity=1, shopfloor=sf)

    ctx = Focus.build_context(sf, now=0.0)
    assert ctx.max_pij == 0.0


def test_focus_context_empty_queue_servers_all_idle_at_t0() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # Jobs added but env hasn't run — server queues are empty.
    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 1.0], due_date=10.0)
    sf.add(j)

    ctx = Focus.build_context(sf, now=0.0)
    assert s1 in ctx.empty_queue_servers
    assert s2 in ctx.empty_queue_servers


def test_focus_context_empty_queue_servers_excludes_loaded_server() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    # First job will be processing; second will be queued behind it.
    j1 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[10.0], due_date=100.0)
    j2 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[10.0], due_date=100.0)
    sf.add(j1)
    sf.add(j2)
    env.run(until=0.01)  # let processes start; j1 grabs server, j2 queues

    ctx = Focus.build_context(sf, now=0.01)
    assert s1 not in ctx.empty_queue_servers  # j2 is queued
    assert len(s1.queue) == 1


def test_focus_context_max_positive_slack_and_pacing() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # j1: due=100, total_p=4 → S=96, |R|=2 → V=48
    # j2: due=20, total_p=4 → S=16, |R|=2 → V=8
    # j3: due=2, total_p=4 → S=-2 (negative, excluded)
    j1 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 2.0], due_date=100.0)
    j2 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 2.0], due_date=20.0)
    j3 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 2.0], due_date=2.0)
    sf.add(j1)
    sf.add(j2)
    sf.add(j3)

    ctx = Focus.build_context(sf, now=0.0)
    assert ctx.max_positive_slack == 96.0
    assert ctx.max_positive_pacing == 48.0


def test_focus_context_includes_psp_jobs_when_passed() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # PSP-only job with a large p_ij that should dominate max_pij.
    psp_job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[42.0], due_date=100.0)
    psp.add(psp_job)

    ctx_without = Focus.build_context(sf, now=0.0)
    ctx_with = Focus.build_context(sf, now=0.0, psp=psp)
    assert ctx_without.max_pij == 0.0
    assert ctx_with.max_pij == 42.0


# ----- pi (SPT mechanism) -----


def test_focus_pi_zero_when_pij_equals_max() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[5.0], due_date=100.0)
    sf.add(job)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0)
    assert focus.pi(job, s1, ctx) == 0.0


def test_focus_pi_one_when_max_pij_zero() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=100.0)
    # Do NOT add the job — keep shopfloor empty so max_pij = 0
    ctx = Focus.build_context(sf, now=0.0)
    assert ctx.max_pij == 0.0

    focus = Focus()
    assert focus.pi(job, s1, ctx) == 1.0


def test_focus_pi_intermediate_value() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # j_short has p_ik=2 at s1; another job has p_ij=8 elsewhere → max=8
    j_short = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[2.0], due_date=100.0)
    j_long = ProductionJob(env=env, sku="A", servers=[s2], processing_times=[8.0], due_date=100.0)
    sf.add(j_short)
    sf.add(j_long)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0)
    assert focus.pi(j_short, s1, ctx) == pytest.approx(1.0 - 2.0 / 8.0)


# ----- omega (Starvation response) -----


def test_focus_omega_zero_at_last_operation() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=100.0)
    sf.add(job)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0)
    # s1 is last in routing → omega = 0
    assert focus.omega(job, s1, ctx) == 0.0


def test_focus_omega_propagates_pi_when_next_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 1.0], due_date=100.0)
    sf.add(job)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0)
    # s2 is empty (no other jobs there) → omega == pi
    assert focus.omega(job, s1, ctx) == focus.pi(job, s1, ctx)


def test_focus_omega_zero_when_next_not_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # Two jobs routed s2 then s1: both will queue/process at s2 first.
    # After a tiny env run, s2 has a user + 1 queued → s2 not in empty_queue_servers
    blocker1 = ProductionJob(env=env, sku="A", servers=[s2, s1], processing_times=[10.0, 1.0], due_date=100.0)
    blocker2 = ProductionJob(env=env, sku="A", servers=[s2, s1], processing_times=[10.0, 1.0], due_date=100.0)
    sf.add(blocker1)
    sf.add(blocker2)
    env.run(until=0.01)
    assert len(s2.queue) >= 1  # at least one queued behind the blocker

    # A different job routed s1 → s2. At s1, "next" is s2; s2 has a queue → omega = 0.
    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 1.0], due_date=100.0)
    sf.add(job)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.01)
    assert s2 not in ctx.empty_queue_servers
    assert focus.omega(job, s1, ctx) == 0.0


# ----- psi (Slack timing) -----


def test_focus_psi_saturates_for_tardy_jobs() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    # Negative slack (already past due even without processing)
    tardy = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=-100.0)
    sf.add(tardy)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0)
    assert focus.psi(tardy, ctx, now=0.0) == 1.0


def test_focus_psi_intermediate_value() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    # urgent: S=5; relaxed: S=50 → max_positive_slack=50
    urgent = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=6.0)  # S=6-0-1=5
    relaxed = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=51.0)  # S=51-0-1=50
    sf.add(urgent)
    sf.add(relaxed)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0)
    # urgent: 1 - 5/50 = 0.9 (high urgency)
    assert focus.psi(urgent, ctx, now=0.0) == pytest.approx(1.0 - 5.0 / 50.0)
    # relaxed: 1 - 50/50 = 0 (least urgent)
    assert focus.psi(relaxed, ctx, now=0.0) == pytest.approx(0.0)


# ----- gamma (Pacing) -----


def test_focus_gamma_saturates_for_tardy_jobs() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    tardy = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=-100.0)
    sf.add(tardy)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0)
    assert focus.gamma(tardy, ctx, now=0.0) == 1.0


def test_focus_gamma_pacing_penalises_long_routing() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    s3 = Server(env=env, capacity=1, shopfloor=sf)

    # Same slack, different |R|:
    # short_route: 1 op,  S=10  → V=10
    # long_route:  3 ops, S=7   (10 - 3 processing) → V=7/3 ≈ 2.33
    # but max_positive_pacing across both = 10 → long_route is "behind" pace
    short_route = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=11.0)
    long_route = ProductionJob(env=env, sku="A", servers=[s1, s2, s3], processing_times=[1.0, 1.0, 1.0], due_date=10.0)
    sf.add(short_route)
    sf.add(long_route)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0)
    gamma_short = focus.gamma(short_route, ctx, now=0.0)
    gamma_long = focus.gamma(long_route, ctx, now=0.0)
    assert gamma_long > gamma_short  # long routing → behind pace → higher impact


# ----- score (weighted aggregate) -----


def test_focus_score_in_unit_interval() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 5.0], due_date=50.0)
    sf.add(j)

    focus = Focus(weights=(0.4, 0.2, 0.3, 0.1))
    ctx = focus.build_context(sf, now=0.0)
    score = focus.score(j, s1, ctx, now=0.0)
    assert 0.0 <= score <= 1.0


def test_focus_score_is_exact_weighted_average() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 5.0], due_date=50.0)
    sf.add(j)

    focus = Focus(weights=(0.4, 0.2, 0.3, 0.1))
    ctx = focus.build_context(sf, now=0.0)
    expected = (
        0.4 * focus.pi(j, s1, ctx)
        + 0.2 * focus.omega(j, s1, ctx)
        + 0.3 * focus.psi(j, ctx, now=0.0)
        + 0.1 * focus.gamma(j, ctx, now=0.0)
    )
    assert focus.score(j, s1, ctx, now=0.0) == pytest.approx(expected)


# ----- FocusPriorityRule adapter -----


def test_focus_priority_rule_returns_negated_score() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 5.0], due_date=50.0)
    sf.add(j)

    focus = Focus()
    adapter = FocusPriorityRule(focus, sf)
    ctx = focus.build_context(sf, now=sf.env.now)
    expected = -focus.score(j, s1, ctx, now=sf.env.now)
    assert adapter(j, s1) == pytest.approx(expected)


def test_focus_priority_rule_includes_psp_jobs_when_provided() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    psp_job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=200.0)
    psp.add(psp_job)
    queued_job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=200.0)
    sf.add(queued_job)

    focus = Focus()
    adapter_with_psp = FocusPriorityRule(focus, sf, psp=psp)
    # max_pij seen should be 100 (from PSP); pi(queued_job, s1) = 1 - 1/100 = 0.99
    score = -adapter_with_psp(queued_job, s1)
    assert score == pytest.approx(focus.score(queued_job, s1, focus.build_context(sf, sf.env.now, psp=psp), sf.env.now))


# ----- refresh_focus_queue -----


def test_refresh_focus_queue_reorders_after_shop_state_change() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    focus = Focus()
    # Use FocusPriorityRule so initial priorities are computed via FOCUS.
    adapter = FocusPriorityRule(focus, sf)

    # Block s1 with a long-running job; queue several behind it.
    blocker = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=5000.0, priority_policy=adapter
    )
    sf.add(blocker)
    env.run(until=0.001)

    j_a = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[5.0], due_date=100.0, priority_policy=adapter)
    j_b = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[2.0], due_date=200.0, priority_policy=adapter)
    sf.add(j_a)
    sf.add(j_b)
    env.run(until=0.01)
    assert len(s1.queue) == 2

    refresh_focus_queue(s1, focus, sf)
    post_keys = [req.key for req in s1.queue]
    assert post_keys == sorted(post_keys, key=lambda k: k[0])
    # And keys actually got recomputed (priorities may differ from pre_keys
    # which were keyed at entry time — at minimum they're consistent).
    for req in s1.queue:
        assert isinstance(req.key, tuple)
        assert len(req.key) == 3


def test_refresh_focus_queue_uses_current_now() -> None:
    """Refreshing should pick up the current env.now, not the entry-time."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    focus = Focus()
    adapter = FocusPriorityRule(focus, sf)

    blocker = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=5000.0, priority_policy=adapter
    )
    sf.add(blocker)

    # An urgent-ish job entered very early
    j = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0, priority_policy=adapter)
    sf.add(j)
    env.run(until=0.01)
    assert len(s1.queue) == 1

    entry_priority = s1.queue[0].key[0]
    env.run(until=5.0)  # advance time without dispatching (blocker still running)
    refresh_focus_queue(s1, focus, sf)
    refreshed_priority = s1.queue[0].key[0]

    # As `now` advances, the slack S_i decreases, so psi → 1 (more urgent),
    # so the negated score becomes smaller (more negative or just smaller).
    # Strict assertion: priorities differ between entry-time and refreshed-time.
    assert not math.isclose(entry_priority, refreshed_priority, rel_tol=1e-9, abs_tol=1e-9)


def test_focus_next_server_after_returns_none_for_unrelated_server() -> None:
    """Defensive: _next_server_after on a server not in routing returns None."""
    from simulatte.dispatching_rules.focus import _next_server_after

    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    s_other = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 1.0], due_date=10.0)
    assert _next_server_after(job, s_other) is None


def test_focus_build_context_skips_completed_jobs() -> None:
    """build_context's `if not remaining: continue` branch: a job in shopfloor.jobs
    with all exit_at timestamps set (mid-cleanup) is skipped from aggregates.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    # Synthetic "completed" job: in shopfloor.jobs but all servers_exit_at set.
    completed = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[5.0], due_date=100.0)
    sf.jobs.add(completed)
    completed.servers_exit_at[s1] = 0.0  # mark as exited

    ctx = Focus.build_context(sf, now=0.0)
    # Completed job contributed nothing; max_pij stays 0
    assert ctx.max_pij == 0.0


def test_focus_psi_returns_one_when_max_positive_slack_zero() -> None:
    """Defensive psi: max_positive_slack=0 → return 1 even for positive-slack jobs."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    # Job NOT in shopfloor.jobs (so it doesn't contribute to ctx) but with positive slack
    j = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)
    # Empty context: no jobs in shop
    ctx = Focus.build_context(sf, now=0.0)
    assert ctx.max_positive_slack == 0.0

    focus = Focus()
    # s_i > 0 but max_positive_slack <= 0 → defensive return 1
    assert focus.psi(j, ctx, now=0.0) == 1.0


def test_focus_gamma_returns_one_when_no_remaining() -> None:
    """Defensive gamma: a job with no remaining operations returns 1."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    completed = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)
    completed.servers_exit_at[s1] = 0.0  # all servers exited

    focus = Focus()
    ctx = Focus.build_context(sf, now=0.0)
    assert focus.gamma(completed, ctx, now=0.0) == 1.0


def test_focus_gamma_returns_one_when_max_positive_pacing_zero() -> None:
    """Defensive gamma: max_positive_pacing=0 → return 1 for positive-pacing jobs."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    j = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)
    ctx = Focus.build_context(sf, now=0.0)
    assert ctx.max_positive_pacing == 0.0

    focus = Focus()
    assert focus.gamma(j, ctx, now=0.0) == 1.0


def test_focus_context_is_frozen_dataclass() -> None:
    ctx = FocusContext(max_pij=1.0, empty_queue_servers=frozenset(), max_positive_slack=0.0, max_positive_pacing=0.0)
    with pytest.raises((AttributeError, Exception)):
        ctx.max_pij = 2.0  # type: ignore[misc]
