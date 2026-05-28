"""Tests for the FOCUS dispatching rule and its adapter."""

from __future__ import annotations

import math

import pytest

from simulatte.dispatching_rules import (
    Focus,
    FocusContext,
    FocusPriorityRule,
)
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def _loaded_two_server_shop() -> tuple[ShopFloor, Server, Server, ProductionJob, ProductionJob]:
    """A 2-server shop with a blocker on s1 and two queued candidates.

    Hand-computed FOCUS values at now=0.0 (blocker is in users -> excluded
    from the candidate set O; only `cand` and `other` are candidates):

      Aggregates: max_pij=8 (other's op), max_positive_slack=20 (cand),
      max_positive_pacing=10 (both jobs tie at V=10).

      cand  (routing s1->s2, p=[4,6], due=30): S=20, V=10
        pi   = 1 - 4/8 = 0.5
        omega= 0.5      (next server s2 has an empty queue; omega == pi)
        psi  = 1 - 20/20 = 0.0
        gamma= 1 - 10/10 = 0.0
        beta = 1.0      (sole balance-improving candidate -> own normalizer)

      other (routing s1, p=[8], due=18): S=10, V=10
        pi   = 1 - 8/8 = 0.0
        psi  = 1 - 10/20 = 0.5
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)
    cand = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[4.0, 6.0], due_date=30.0)
    other = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[8.0], due_date=18.0)
    sf.add(cand)
    sf.add(other)
    env.run(until=0.002)
    return sf, s1, s2, cand, other


# ----- Init validation -----


def test_focus_init_validates_weights_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        Focus(weights=(0.5, 0.5, 0.5, 0.5, 0.5))


def test_focus_init_validates_weights_count() -> None:
    with pytest.raises(ValueError, match="5 elements"):
        Focus(weights=(0.5, 0.5))  # type: ignore[arg-type]


def test_focus_init_validates_weights_range() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        Focus(weights=(-0.1, 0.5, 0.3, 0.3, 0.0))


def test_focus_init_accepts_default_weights() -> None:
    focus = Focus()
    assert focus.w1 == focus.w2 == focus.w3 == focus.w4 == 0.25
    assert focus.w5 == 0.0


def test_focus_init_accepts_all_five_active() -> None:
    focus = Focus(weights=(0.2, 0.2, 0.2, 0.2, 0.2))
    assert focus.w5 == pytest.approx(0.2)


def test_focus_init_accepts_zero_weights() -> None:
    """A weight of 0 disables the corresponding mechanism; non-zeros must sum to 1."""
    focus = Focus(weights=(0.0, 0.0, 0.0, 0.0, 1.0))
    assert focus.w5 == 1.0
    focus2 = Focus(weights=(0.5, 0.5, 0.0, 0.0, 0.0))
    assert focus2.w1 == focus2.w2 == 0.5


# ----- FocusContext aggregates -----


def test_focus_context_max_pij_basic() -> None:
    # O = jobs currently in server queues (candidates). Jobs being *processed*
    # (in users) are excluded — their load is already in workloads.
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # j1 will grab s1's user slot; j2 and j3 will queue behind it.
    # j2: p=[5, 2] → remaining ops have max 5.  j3: p=[3, 9] → remaining op max 9.
    # After env.run: j1 in users (excluded), j2 and j3 in queue (candidates).
    j1 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1000.0, 1.0], due_date=10000.0)
    j2 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[5.0, 2.0], due_date=100.0)
    j3 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 9.0], due_date=100.0)
    sf.add(j1)
    sf.add(j2)
    sf.add(j3)
    env.run(until=0.001)
    assert len(s1.queue) == 2  # j2 and j3 queued

    ctx = Focus.build_context(sf, now=0.001)
    # max over candidates j2 (ops: 5,2) and j3 (ops: 3,9) → 9.0
    assert ctx.max_pij == 9.0


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
    # O = jobs in server queues only (candidates). A blocker holds s1's user slot;
    # j1, j2, j3 queue behind it and form the candidate set.
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # Blocker: long job grabs s1 so the three candidates must queue.
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)

    # Candidates (queued at s1):
    # j1: due=100, total_p=4 → S=100-0.001-4=95.999, |R|=2 → V≈48
    # j2: due=20, total_p=4 → S=20-0.001-4=15.999, |R|=2 → V≈8
    # j3: due=2, total_p=4 → S=2-0.001-4<0 (negative, excluded)
    j1 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 2.0], due_date=100.0)
    j2 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 2.0], due_date=20.0)
    j3 = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 2.0], due_date=2.0)
    sf.add(j1)
    sf.add(j2)
    sf.add(j3)
    env.run(until=0.002)
    assert len(s1.queue) == 3  # all three queued behind blocker

    now = 0.002
    ctx = Focus.build_context(sf, now=now)
    # j1: S = 100 - now - 4 ≈ 95.998 (largest positive slack)
    # j2: S = 20 - now - 4 ≈ 15.998 (positive but smaller)
    # j3: S = 2 - now - 4 < 0 (excluded)
    expected_slack = 100.0 - now - 4.0  # j1 dominates
    expected_pacing = expected_slack / 2  # |R| = 2 for j1
    assert ctx.max_positive_slack == pytest.approx(expected_slack)
    assert ctx.max_positive_pacing == pytest.approx(expected_pacing)


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
    # job must be a candidate (in queue) for max_pij to reflect it.
    # Use capacity=2 so job enters queue rather than users when added alone.
    env = Environment()
    sf = ShopFloor(env=env)
    # capacity=2 but we use a blocker to force job into queue
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    # Blocker grabs the single slot; job queues behind it.
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)

    job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[5.0], due_date=100.0)
    sf.add(job)
    env.run(until=0.002)
    assert len(s1.queue) == 1  # job queued as candidate

    focus = Focus()
    ctx = focus.build_context(sf, now=0.002)
    # Only candidate is `job` with p=5 → max_pij=5, pi = 1 - 5/5 = 0.
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
    # Both j_short and j_long must be candidates (in queues).
    # Use blockers to force each into its respective queue.
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # Block both servers so the test jobs queue as candidates.
    b1 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=10000.0)
    b2 = ProductionJob(env=env, sku="A", servers=[s2], processing_times=[1000.0], due_date=10000.0)
    sf.add(b1)
    sf.add(b2)
    env.run(until=0.001)

    # j_short has p_ik=2 at s1; j_long has p_ij=8 at s2 → max over candidates = 8
    j_short = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[2.0], due_date=100.0)
    j_long = ProductionJob(env=env, sku="A", servers=[s2], processing_times=[8.0], due_date=100.0)
    sf.add(j_short)
    sf.add(j_long)
    env.run(until=0.002)
    assert len(s1.queue) == 1 and len(s2.queue) == 1

    focus = Focus()
    ctx = focus.build_context(sf, now=0.002)
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
    # Both jobs must be candidates (in queue). Use a blocker to force queuing.
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)

    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)

    # At now=0.001: urgent S=6-0.001-1=4.999; relaxed S=51-0.001-1=49.999
    # max_positive_slack = 49.999 (from relaxed)
    urgent = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=6.0)
    relaxed = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=51.0)
    sf.add(urgent)
    sf.add(relaxed)
    env.run(until=0.002)
    assert len(s1.queue) == 2

    now = 0.002
    focus = Focus()
    ctx = focus.build_context(sf, now=now)
    s_urgent = 6.0 - now - 1.0  # S_urgent
    s_relaxed = 51.0 - now - 1.0  # S_relaxed = max_positive_slack
    assert ctx.max_positive_slack == pytest.approx(s_relaxed)
    # urgent: 1 - s_urgent / s_relaxed
    assert focus.psi(urgent, ctx, now=now) == pytest.approx(1.0 - s_urgent / s_relaxed)
    # relaxed: 1 - s_relaxed / s_relaxed = 0
    assert focus.psi(relaxed, ctx, now=now) == pytest.approx(0.0)


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
    # Both jobs must be candidates (in queue). Blocker forces them to queue.
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    s3 = Server(env=env, capacity=1, shopfloor=sf)

    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)

    # Same slack, different |R|:
    # short_route: 1 op,  S≈10  → V≈10
    # long_route:  3 ops, S≈7   (10 - 3 processing) → V≈7/3 ≈ 2.33
    # max_positive_pacing ≈ 10 → long_route is "behind" pace
    short_route = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=11.0)
    long_route = ProductionJob(env=env, sku="A", servers=[s1, s2, s3], processing_times=[1.0, 1.0, 1.0], due_date=10.0)
    sf.add(short_route)
    sf.add(long_route)
    env.run(until=0.002)
    assert len(s1.queue) == 2

    now = 0.002
    focus = Focus()
    ctx = focus.build_context(sf, now=now)
    gamma_short = focus.gamma(short_route, ctx, now=now)
    gamma_long = focus.gamma(long_route, ctx, now=now)
    assert gamma_long > gamma_short  # long routing → behind pace → higher impact


# ----- score (weighted aggregate) -----


def test_focus_score_in_unit_interval() -> None:
    # j must be a candidate (in queue) for ctx aggregates to be non-trivial.
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)

    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 5.0], due_date=50.0)
    sf.add(j)
    env.run(until=0.002)
    assert len(s1.queue) == 1

    now = 0.002
    focus = Focus(weights=(0.4, 0.2, 0.3, 0.1, 0.0))
    ctx = focus.build_context(sf, now=now)
    score = focus.score(j, s1, ctx, now=now)
    assert 0.0 <= score <= 1.0


def test_focus_score_is_exact_weighted_average() -> None:
    """Score equals the hand-computed weighted average of independent constants."""
    sf, s1, _s2, cand, _other = _loaded_two_server_shop()

    focus = Focus(weights=(0.3, 0.2, 0.2, 0.1, 0.2))
    ctx = focus.build_context(sf, now=0.0)
    # pi=0.5, omega=0.5, psi=0.0, gamma=0.0, beta=1.0 (see helper docstring).
    expected = 0.3 * 0.5 + 0.2 * 0.5 + 0.2 * 0.0 + 0.1 * 0.0 + 0.2 * 1.0  # = 0.45
    assert focus.score(cand, s1, ctx, now=0.0) == pytest.approx(0.45)
    assert expected == pytest.approx(0.45)


def test_focus_score_pi_only_equals_known_constant() -> None:
    """weights=(1,0,0,0,0) -> score == pi == 0.5 for cand (independent constant)."""
    sf, s1, _s2, cand, _other = _loaded_two_server_shop()
    focus = Focus(weights=(1.0, 0.0, 0.0, 0.0, 0.0))
    ctx = focus.build_context(sf, now=0.0)
    assert focus.score(cand, s1, ctx, now=0.0) == pytest.approx(0.5)


def test_focus_score_psi_only_equals_known_constant() -> None:
    """weights=(0,0,1,0,0) -> score == psi == 0.5 for other (independent constant)."""
    sf, s1, _s2, _cand, other = _loaded_two_server_shop()
    focus = Focus(weights=(0.0, 0.0, 1.0, 0.0, 0.0))
    ctx = focus.build_context(sf, now=0.0)
    assert focus.score(other, s1, ctx, now=0.0) == pytest.approx(0.5)


def test_focus_score_beta_off_matches_four_mechanism_sum() -> None:
    """With w5=0, score equals exactly the pi/omega/psi/gamma weighted sum (regression)."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 5.0], due_date=50.0)
    sf.add(j)

    focus = Focus(weights=(0.4, 0.2, 0.3, 0.1, 0.0))
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
    ctx = FocusContext(
        max_pij=1.0,
        empty_queue_servers=frozenset(),
        max_positive_slack=0.0,
        max_positive_pacing=0.0,
        workloads=(),
        server_index={},
        pre_entropy=0.0,
        max_positive_c=0.0,
    )
    with pytest.raises((AttributeError, Exception)):
        ctx.max_pij = 2.0  # type: ignore[misc]


# ----- _entropy helper -----


def test_entropy_all_zero_vector_returns_zero() -> None:
    from simulatte.dispatching_rules.focus import _entropy

    assert _entropy([0.0, 0.0, 0.0]) == 0.0


def test_entropy_uniform_vector_equals_log_n() -> None:
    from simulatte.dispatching_rules.focus import _entropy

    assert _entropy([1.0, 1.0, 1.0, 1.0]) == pytest.approx(math.log(4))


def test_entropy_one_hot_vector_returns_zero() -> None:
    from simulatte.dispatching_rules.focus import _entropy

    assert _entropy([0.0, 5.0, 0.0]) == pytest.approx(0.0)


def test_entropy_mixed_matches_hand_computed() -> None:
    from simulatte.dispatching_rules.focus import _entropy

    # Two-bin: p=[0.25, 0.75], H = -(0.25*ln 0.25 + 0.75*ln 0.75)
    expected = -(0.25 * math.log(0.25) + 0.75 * math.log(0.75))
    assert _entropy([1.0, 3.0]) == pytest.approx(expected)


# ----- FocusContext beta aggregates -----


def test_focus_context_workloads_empty_shop() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    Server(env=env, capacity=1, shopfloor=sf)
    Server(env=env, capacity=1, shopfloor=sf)

    ctx = Focus.build_context(sf, now=0.0)
    assert ctx.workloads == (0.0, 0.0)
    assert ctx.pre_entropy == 0.0
    assert ctx.max_positive_c == 0.0


def test_focus_context_workloads_queue_and_users() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # blocker at s1: gets the user slot, p=1000
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1000.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)

    # j_q queues behind blocker at s1 (p_s1=7), routes to s2 next (p_s2=3)
    j_q = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[7.0, 3.0], due_date=200.0)
    sf.add(j_q)
    env.run(until=0.002)
    assert len(s1.queue) == 1  # j_q queued

    ctx = Focus.build_context(sf, now=0.002)
    s1_idx = ctx.server_index[s1]
    s2_idx = ctx.server_index[s2]
    # blocker (user, 1000) + j_q (queue, 7) = 1007 at s1
    assert ctx.workloads[s1_idx] == pytest.approx(1007.0)
    # j_q has not yet entered s2 → s2 workload is 0
    assert ctx.workloads[s2_idx] == 0.0


def test_focus_context_pre_entropy_balanced_two_server_shop() -> None:
    """Two servers with equal workload → e_minus = ln(2)."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # Block both servers with equal-length processing jobs
    b1 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[10.0], due_date=100.0)
    b2 = ProductionJob(env=env, sku="A", servers=[s2], processing_times=[10.0], due_date=100.0)
    sf.add(b1)
    sf.add(b2)
    env.run(until=0.001)

    ctx = Focus.build_context(sf, now=0.001)
    assert ctx.pre_entropy == pytest.approx(math.log(2))


# ----- beta semantics -----


def test_focus_beta_returns_zero_for_idle_shop() -> None:
    """Empty shop: pre_entropy=0, every c(i)=0 → beta=0 for all candidates."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 4.0], due_date=100.0)
    sf.add(j)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0)
    # Job is in queue at s1 with no other work → workload at s1=3, elsewhere 0.
    # pre_entropy = entropy of [3, 0] = 0 (one-hot). Dispatch perturbation
    # makes W'[s1] = max(0, 3-3) = 0, W'[s2] = 0+4 = 4 → another one-hot → e_i=0.
    # c_i = 0 → beta = 0.
    assert focus.beta(j, s1, ctx) == 0.0


def test_focus_beta_returns_one_when_candidate_only_positive() -> None:
    """If only the candidate has c(i') > 0, beta = c/c = 1.

    Scenario: s1 heavily loaded (W=110), s2 idle (W=0).
    - blocker at s1 (last op) → dispatch only decrements s1 → still one-hot → c=0.
    - candidate `j` (s1→s2) → dispatch pulls p_i,s1=10 from s1, adds p_i,s2=10 to s2
      → vector becomes [100, 10] → positive entropy → c > 0.
    Since only `j` has positive c, max_positive_c = c(j) → beta(j) = 1.
    """
    from simulatte.dispatching_rules.focus import _delta_entropy

    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.001)

    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[10.0, 10.0], due_date=200.0)
    sf.add(j)
    env.run(until=0.002)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.002)

    c_candidate = _delta_entropy(
        job=j,
        server=s1,
        workloads=ctx.workloads,
        server_index=ctx.server_index,
        pre_entropy=ctx.pre_entropy,
    )
    assert c_candidate > 0.0
    assert ctx.max_positive_c == pytest.approx(c_candidate)
    assert focus.beta(j, s1, ctx) == pytest.approx(1.0)


def test_focus_beta_returns_zero_when_c_non_positive() -> None:
    """When dispatching the candidate worsens (or doesn't change) balance, beta = 0."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # Two balanced loaded servers → any dispatch creates imbalance → c_i ≤ 0.
    b1 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[10.0], due_date=100.0)
    b2 = ProductionJob(env=env, sku="A", servers=[s2], processing_times=[10.0], due_date=100.0)
    sf.add(b1)
    sf.add(b2)
    env.run(until=0.001)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.001)
    # b1 is at s1 (last op) → dispatching b1 only does -p_ik at s1 → vector becomes [0, 10] → e=0 < ln(2) → c<0
    assert focus.beta(b1, s1, ctx) == 0.0


def test_focus_beta_returns_zero_when_max_positive_c_nonpositive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive guard: when c_i > 0 but ctx.max_positive_c is non-positive, return 0 (not ZeroDivisionError)."""
    from simulatte.dispatching_rules import focus as focus_module

    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[5.0, 5.0], due_date=100.0)

    # Force c_i > 0 by patching _delta_entropy; meanwhile hand-craft ctx with max_positive_c = 0.
    monkeypatch.setattr(
        focus_module,
        "_delta_entropy",
        lambda *, job, server, workloads, server_index, pre_entropy: 1.0,
    )

    ctx = FocusContext(
        max_pij=5.0,
        empty_queue_servers=frozenset(),
        max_positive_slack=0.0,
        max_positive_pacing=0.0,
        workloads=(0.0, 0.0),
        server_index={s1: 0, s2: 1},
        pre_entropy=0.0,
        max_positive_c=0.0,
    )

    focus = Focus()
    assert focus.beta(job, s1, ctx) == 0.0


def test_focus_beta_last_op_no_u_term() -> None:
    """For a last-operation candidate, only the -p_ik term applies (no +p_iu)."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    Server(env=env, capacity=1, shopfloor=sf)  # s2: registered for shop index, unreferenced

    # Imbalanced shop: s1 heavily loaded, s2 idle.
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.001)

    # Last-op candidate at s1 (only one server in routing)
    last_op = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[20.0], due_date=200.0)
    sf.add(last_op)
    env.run(until=0.002)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.002)
    # W = [120, 0]. Dispatch last_op at s1: W' = [120-20, 0] = [100, 0]. Still one-hot. e_i = 0.
    # pre_entropy = entropy of [120, 0] = 0. c_i = 0 → beta = 0.
    assert focus.beta(last_op, s1, ctx) == 0.0


def test_focus_beta_psp_candidate_clamps() -> None:
    """PSP candidate where W[k] < p_ik: clamp keeps W'[k] >= 0."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # All servers idle → W = [0, 0]
    psp_job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[5.0, 3.0], due_date=100.0)
    psp.add(psp_job)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.0, psp=psp)
    # The clamp prevents NaN: should not raise, and beta should return 0
    # (release into idle k creates imbalance, c_i ≤ 0).
    result = focus.beta(psp_job, s1, ctx)
    assert math.isfinite(result)
    assert result == 0.0


def test_focus_beta_positive_when_balance_improves() -> None:
    """Concrete scenario: candidate's dispatch increases entropy → positive beta."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # s1 heavily loaded (W=100), s2 idle (W=0). Imbalanced.
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.001)

    # A two-op job at s1 with small p_ik=10 and big p_iu=40 → dispatching moves 10
    # from s1 (still 90 left) and adds 40 to s2 → balance improves a lot.
    rebal = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[10.0, 40.0], due_date=200.0)
    sf.add(rebal)
    env.run(until=0.002)

    focus = Focus()
    ctx = focus.build_context(sf, now=0.002)
    assert focus.beta(rebal, s1, ctx) > 0.0
    assert focus.beta(rebal, s1, ctx) <= 1.0


# ----- score: beta only -----


def test_focus_score_beta_only_equals_beta() -> None:
    """With weights=(0,0,0,0,1), score == beta exactly."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.001)

    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[10.0, 40.0], due_date=200.0)
    sf.add(j)
    env.run(until=0.002)

    focus = Focus(weights=(0.0, 0.0, 0.0, 0.0, 1.0))
    ctx = focus.build_context(sf, now=0.002)
    assert focus.score(j, s1, ctx, now=0.002) == pytest.approx(focus.beta(j, s1, ctx))


# ----- FocusPriorityRule: liveness (ctx rebuilt per call) -----


def test_focus_build_context_skips_beta_pass_when_disabled() -> None:
    """compute_beta=False gates only the beta normalizer (max_positive_c)."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # Blocker holds s1; the queued job's s1->s2 routing moves load to the
    # empty server, improving balance => c(i) > 0 under the full pass.
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)
    rebal = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[10.0, 40.0], due_date=200.0)
    sf.add(rebal)
    env.run(until=0.002)

    ctx_full = Focus.build_context(sf, now=0.002, compute_beta=True)
    ctx_skip = Focus.build_context(sf, now=0.002, compute_beta=False)

    assert ctx_full.max_positive_c > 0.0
    assert ctx_skip.max_positive_c == 0.0
    # Non-beta aggregates are identical — only the beta normalizer is gated.
    assert ctx_skip.max_pij == ctx_full.max_pij
    assert ctx_skip.max_positive_slack == ctx_full.max_positive_slack
    assert ctx_skip.max_positive_pacing == ctx_full.max_positive_pacing
    # A direct beta() call on a gated context is safe (max_positive_c<=0 guard).
    focus_beta = Focus(weights=(0.2, 0.2, 0.2, 0.2, 0.2))
    assert focus_beta.beta(rebal, s1, ctx_skip) == 0.0


def test_focus_score_identical_with_beta_off_regardless_of_compute_beta() -> None:
    """With w5=0, the score is identical whether ctx skipped the beta pass."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    blocker = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=10000.0)
    sf.add(blocker)
    env.run(until=0.001)
    j = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[10.0, 40.0], due_date=200.0)
    sf.add(j)
    env.run(until=0.002)

    focus = Focus()  # default weights -> w5 == 0
    ctx_full = focus.build_context(sf, now=0.002, compute_beta=True)
    ctx_skip = focus.build_context(sf, now=0.002, compute_beta=False)
    assert focus.score(j, s1, ctx_skip, now=0.002) == pytest.approx(focus.score(j, s1, ctx_full, now=0.002))


def test_focus_priority_rule_rebuilds_ctx_per_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """FocusPriorityRule rebuilds ctx on every call — no stale-closure leak.

    Main's :meth:`~simulatte.server.Server.sort_queue` calls
    ``job.priority_policy(job, server)`` before every dispatch. When the
    same adapter is used across multiple servers in a multi-server routing,
    each call must build a fresh FocusContext against the *current* server
    and shopfloor state — not a snapshot frozen at an earlier instant.

    Regression guard: if a stale-closure were reintroduced (e.g.
    ``adapter(job, s1)`` caches ctx and reuses it for ``adapter(job, s2)``),
    ``build_context`` would be called only ONCE and the assertion would fail.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 5.0], due_date=50.0)
    sf.add(job)

    focus = Focus()
    adapter = FocusPriorityRule(focus, sf)

    call_count = 0
    real_build = Focus.build_context

    def counting_build(shopfloor, now, *, psp=None, compute_beta=True):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return real_build(shopfloor, now, psp=psp, compute_beta=compute_beta)

    monkeypatch.setattr(Focus, "build_context", staticmethod(counting_build))

    adapter(job, s1)
    adapter(job, s2)

    assert call_count == 2, (
        f"Expected build_context to be called twice (once per server), got {call_count}. "
        "A stale-closure regression would produce call_count == 1."
    )


# ----- build_focus_system builder -----


def test_build_focus_system_runs_and_completes_jobs() -> None:
    from simulatte.builders import build_focus_system

    env = Environment()
    psp, servers, shopfloor, router = build_focus_system(env, n_servers=4, arrival_rate=1.0)
    assert psp is None  # push system
    assert len(servers) == 4
    env.run(until=200.0)
    assert len(shopfloor.jobs_done) > 0
