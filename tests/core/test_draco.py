"""Tests for the DRACO non-hierarchical release/dispatch policy."""

from __future__ import annotations

import pytest

from simulatte.builders import build_draco_system
from simulatte.dispatching_rules.focus import Focus
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.draco import Draco
from simulatte.policies.triggers import on_completion_trigger
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


# ----- Constructor / validation -----


def test_draco_init_validates_total_impact_weights_sum() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    with pytest.raises(ValueError, match="sum to 1"):
        Draco(shopfloor=sf, total_impact_weights=(0.5, 0.5, 0.5), wip_target=10, loop_target=5)


def test_draco_init_validates_total_impact_weights_count() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    with pytest.raises(ValueError, match="3 elements"):
        Draco(shopfloor=sf, total_impact_weights=(0.5, 0.5), wip_target=10, loop_target=5)  # type: ignore[arg-type]


def test_draco_init_validates_total_impact_weights_range() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        Draco(shopfloor=sf, total_impact_weights=(-0.1, 0.6, 0.5), wip_target=10, loop_target=5)


def test_draco_init_validates_wip_target() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    with pytest.raises(ValueError, match="wip_target"):
        Draco(shopfloor=sf, wip_target=0, loop_target=5)


def test_draco_init_validates_loop_target_scalar() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    with pytest.raises(ValueError, match="loop_target"):
        Draco(shopfloor=sf, wip_target=10, loop_target=0)


def test_draco_init_validates_loop_target_dict_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    with pytest.raises(ValueError, match="loop_target dict"):
        Draco(shopfloor=sf, wip_target=10, loop_target={})


def test_draco_init_validates_loop_target_dict_values() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    with pytest.raises(ValueError, match=r"loop_target values"):
        Draco(shopfloor=sf, wip_target=10, loop_target={(s1, s2): 0})


def test_draco_init_accepts_default_focus_and_impact_weights() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)
    assert draco.tau == 10
    assert draco.loop_target == 5


# ----- _count_wip -----


def test_draco_count_wip_zero_when_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    Server(env=env, capacity=1, shopfloor=sf)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)
    assert draco._count_wip() == 0


def test_draco_count_wip_uses_count_not_workload() -> None:
    """Spec §3.1: W = Σ(|Q_j| + |H_j|), in job units — not workload."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)

    # Three jobs, processing time 100 each (workload would dominate count)
    j1 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=1000.0)
    j2 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=1000.0)
    j3 = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[100.0], due_date=1000.0)
    sf.add(j1)
    sf.add(j2)
    sf.add(j3)
    env.run(until=0.01)

    # One in use + two queued = 3 jobs (NOT 300, which would be workload)
    assert draco._count_wip() == 3


# ----- ro^P and ro^Q -----


def test_draco_ro_P_zero_at_or_above_two_tau() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    draco = Draco(shopfloor=sf, wip_target=5, loop_target=3)
    assert draco._ro_P(10) == 0.0
    assert draco._ro_P(100) == 0.0  # saturated


def test_draco_ro_P_one_at_wip_zero() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    draco = Draco(shopfloor=sf, wip_target=5, loop_target=3)
    assert draco._ro_P(0) == 1.0


def test_draco_ro_Q_zero_at_wip_zero() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    draco = Draco(shopfloor=sf, wip_target=5, loop_target=3)
    assert draco._ro_Q(0) == 0.0


def test_draco_ro_Q_one_at_or_above_two_tau() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    draco = Draco(shopfloor=sf, wip_target=5, loop_target=3)
    assert draco._ro_Q(10) == 1.0
    assert draco._ro_Q(100) == 1.0  # saturated


def test_draco_ro_at_wip_tau() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=3)
    assert draco._ro_P(10) == pytest.approx(0.5)
    assert draco._ro_Q(10) == pytest.approx(0.5)


# ----- _authorization_impact -----


def test_draco_authorization_returns_one_at_last_operation() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)

    job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)
    assert draco._authorization_impact(job, s1) == 1.0


def test_draco_authorization_zero_when_loop_at_target() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    # Fill s2 with one in-use job (count=1, queue=0)
    blocker = ProductionJob(env=env, sku="A", servers=[s2], processing_times=[100.0], due_date=200.0)
    sf.add(blocker)
    env.run(until=0.01)
    assert s2.count == 1

    draco = Draco(shopfloor=sf, wip_target=10, loop_target=1)
    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 1.0], due_date=10.0)
    # a_{s1,s2} = s1.count(0) + s2.queue(0) + s2.count(1) = 1; eps=1 → A=0
    assert draco._authorization_impact(job, s1) == 0.0


def test_draco_authorization_partial_value() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    blocker = ProductionJob(env=env, sku="A", servers=[s2], processing_times=[100.0], due_date=200.0)
    sf.add(blocker)
    env.run(until=0.01)

    draco = Draco(shopfloor=sf, wip_target=10, loop_target=4)
    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 1.0], due_date=10.0)
    # a = 1, eps = 4 → A = 1 - 1/4 = 0.75
    assert draco._authorization_impact(job, s1) == pytest.approx(0.75)


def test_draco_authorization_uses_per_pair_dict_when_provided() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    blocker = ProductionJob(env=env, sku="A", servers=[s2], processing_times=[100.0], due_date=200.0)
    sf.add(blocker)
    env.run(until=0.01)

    draco = Draco(shopfloor=sf, wip_target=10, loop_target={(s1, s2): 4})
    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 1.0], due_date=10.0)
    assert draco._authorization_impact(job, s1) == pytest.approx(0.75)


# ----- priority_policy force flag -----


def test_draco_priority_policy_returns_neg_inf_when_forced() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)
    job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)

    draco._forced_at_server[s1] = job  # type: ignore[assignment]
    assert draco.priority_policy(job, s1) == float("-inf")
    # Flag persists — it is cleared only at the start of the next decide_next_job
    assert s1 in draco._forced_at_server
    assert draco._forced_at_server[s1] is job


def test_draco_priority_policy_returns_normal_score_when_not_forced() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)
    job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)
    sf.add(job)

    # No flag set → normal score (finite, negated)
    score = draco.priority_policy(job, s1)
    assert score != float("-inf")
    assert score > -1.0  # negated DRACO score is in [-1, 0]
    assert score <= 0.0


def test_draco_priority_policy_flag_persists_across_calls() -> None:
    """Flag stays set across repeated priority_policy calls while active.

    Under the persistent-flag design, successive calls at the same
    (job, server) both return -inf while the flag is set.  The flag is
    cleared only at the start of the next decide_next_job for that server.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    draco = Draco(shopfloor=sf, psp=psp, wip_target=10, loop_target=5)
    job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)
    sf.add(job)

    # Set the flag and verify it persists across multiple priority_policy calls.
    draco._forced_at_server[s1] = job  # type: ignore[assignment]
    first = draco.priority_policy(job, s1)
    second = draco.priority_policy(job, s1)
    assert first == float("-inf")
    assert second == float("-inf")
    assert s1 in draco._forced_at_server

    # Simulate a new completion at s1: decide_next_job must clear the flag.
    # Re-arm the flag and then call decide_next_job with a minimal fake
    # triggering job (no queue/PSP candidates, so it pops and returns early).
    draco._forced_at_server[s1] = job  # type: ignore[assignment]  # re-arm

    class _FakeTrigger:
        previous_server = s1

    draco.decide_next_job(_FakeTrigger(), psp)  # type: ignore[arg-type]
    assert s1 not in draco._forced_at_server


def test_draco_priority_policy_force_flag_is_per_server() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)
    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 1.0], due_date=10.0)
    sf.add(job)

    draco._forced_at_server[s1] = job  # type: ignore[assignment]
    # Forced at s1 only — s2 returns normal score
    assert draco.priority_policy(job, s2) != float("-inf")
    assert draco.priority_policy(job, s1) == float("-inf")


# ----- decide_next_job: integration -----


def _policy_factory(draco: Draco):  # noqa: ANN202
    def policy(job: ProductionJob, server: Server) -> float:
        return draco.priority_policy(job, server)

    return policy


def test_draco_decide_next_job_empty_no_op() -> None:
    """No queue, no PSP candidates → decide_next_job returns without action."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    draco = Draco(shopfloor=sf, psp=psp, wip_target=10, loop_target=5)
    env.process(on_completion_trigger(sf, psp, draco.decide_next_job))

    job = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)
    sf.add(job)
    env.run()

    # No errors, no PSP releases, no force-flag leakage
    assert not draco._forced_at_server
    assert job.servers_exit_at[s1] == 1.0


def test_draco_psp_winner_processes_immediately() -> None:
    """Headline test: a DRACO-elected PSP winner is the immediate next dispatch.

    A short, somewhat-urgent PSP job is scored higher than a long, relaxed
    queued job. After the currently-processing job exits, the PSP job must
    enter and exit server s1 BEFORE the queued job.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    draco = Draco(shopfloor=sf, psp=psp, wip_target=10, loop_target=5)
    env.process(on_completion_trigger(sf, psp, draco.decide_next_job))
    policy = _policy_factory(draco)

    current = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[5.0], due_date=100.0, priority_policy=policy
    )
    queued = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[20.0], due_date=200.0, priority_policy=policy
    )
    sf.add(current)
    sf.add(queued)

    psp_job = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=50.0, priority_policy=policy
    )
    psp.add(psp_job)

    env.run()

    psp_exit = psp_job.servers_exit_at[s1]
    queued_exit = queued.servers_exit_at[s1]
    assert psp_exit is not None
    assert queued_exit is not None
    assert psp_exit < queued_exit
    # psp_job should start at t=5 (immediately after current) and finish at t=6
    assert psp_job.servers_entry_at[s1] == 5.0
    assert psp_exit == 6.0


def test_draco_queue_winner_dispatched_correctly() -> None:
    """When the best score is already in Q_k, no PSP release happens.

    Heavy queue-side R term + already-late queued job dominates; PSP
    candidate stays in PSP for this decision.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    # Tiny tau → shop is over-target → ro^Q is high, ro^P is low
    draco = Draco(shopfloor=sf, psp=psp, wip_target=1, loop_target=5, total_impact_weights=(0.7, 0.15, 0.15))
    env.process(on_completion_trigger(sf, psp, draco.decide_next_job))
    policy = _policy_factory(draco)

    current = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[5.0], due_date=100.0, priority_policy=policy
    )
    queued_urgent = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=6.0, priority_policy=policy
    )
    psp_relaxed = ProductionJob(
        env=env,
        sku="A",
        servers=[s1],
        processing_times=[100.0],
        due_date=10000.0,
        priority_policy=policy,
    )
    sf.add(current)
    sf.add(queued_urgent)
    psp.add(psp_relaxed)

    env.run(until=5.5)

    # First DRACO decision at t=5 picked queued_urgent (queue winner).
    # No PSP release should have happened at that decision.
    assert queued_urgent in s1.current_jobs
    assert psp_relaxed in psp
    # (A later DRACO trigger at queued_urgent's t=6 completion would then
    # release psp_relaxed since it's the only remaining candidate; that's
    # outside the scope of this test, which is about the t=5 decision.)


def test_draco_winner_via_R_boost_still_processes_first() -> None:
    """The corner case that motivates the `_forced_at_server` flag.

    PSP candidate has lower A+D than a queued candidate but wins DRACO
    via the R boost (under-target shop, high w^R). The force flag must
    ensure the PSP winner is dispatched before the queued candidate
    despite the queue-side ordering favouring the queued one.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    # Large tau → ro^P boost is large; heavy w^R magnifies it.
    draco = Draco(
        shopfloor=sf,
        psp=psp,
        focus_weights=(0.25, 0.25, 0.25, 0.25, 0.0),
        total_impact_weights=(0.7, 0.15, 0.15),
        wip_target=20,
        loop_target=5,
    )
    env.process(on_completion_trigger(sf, psp, draco.decide_next_job))
    policy = _policy_factory(draco)

    # Currently processing — finishes at t=10
    current = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[10.0], due_date=100.0, priority_policy=policy
    )
    # Queued URGENT (high A+D would normally dominate queue ordering)
    queued_urgent = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=5.0, priority_policy=policy
    )
    # PSP RELAXED, long processing — low A+D, but R-boost wins
    psp_relaxed = ProductionJob(
        env=env, sku="A", servers=[s1], processing_times=[50.0], due_date=1000.0, priority_policy=policy
    )
    sf.add(current)
    sf.add(queued_urgent)
    psp.add(psp_relaxed)

    env.run()

    # If the force flag works: psp_relaxed runs at t=10..60, then queued at t=60..61
    psp_exit = psp_relaxed.servers_exit_at[s1]
    queued_exit = queued_urgent.servers_exit_at[s1]
    assert psp_exit is not None
    assert queued_exit is not None
    assert psp_exit < queued_exit
    assert psp_relaxed.servers_entry_at[s1] == 10.0


def test_draco_decide_next_job_returns_when_no_previous_server() -> None:
    """Defensive: triggering_job with previous_server=None is a no-op."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    draco = Draco(shopfloor=sf, psp=psp, wip_target=10, loop_target=5)

    # A job that has never visited any server — previous_server is None
    fresh = ProductionJob(env=env, sku="A", servers=[s1], processing_times=[1.0], due_date=10.0)
    assert fresh.previous_server is None
    draco.decide_next_job(fresh, psp)  # should not raise
    assert not draco._forced_at_server


# ----- build_draco_system -----


def test_build_draco_system_returns_pull_system_quadruple() -> None:
    env = Environment()
    psp, servers, shop_floor, router = build_draco_system(env, wip_target=8, loop_target=4)
    assert isinstance(psp, PreShopPool)
    assert len(servers) == 6
    assert all(isinstance(s, Server) for s in servers)
    assert shop_floor is not None
    assert router is not None


def test_build_draco_system_wires_starvation_avoidance() -> None:
    """A job arriving in PSP whose first server is idle must be released immediately."""
    env = Environment()
    psp, servers, shop_floor, _router = build_draco_system(env, wip_target=8, loop_target=4)

    # All servers idle at t=0 → a synthetic PSP arrival should be auto-released.
    job = ProductionJob(env=env, sku="A", servers=[servers[0]], processing_times=[1.0], due_date=10.0)
    psp.add(job)
    # Starvation avoidance fires synchronously inside psp.add → job should be gone.
    assert job not in psp
    assert job in shop_floor.jobs


# ----- focus_weights (5-tuple, beta) integration -----


def test_draco_default_focus_weights_disables_beta() -> None:
    """Default focus_weights should keep beta dormant (w5 = 0.0)."""
    env = Environment()
    sf = ShopFloor(env=env)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)
    assert draco.focus.w5 == 0.0
    assert draco.focus.w1 == draco.focus.w2 == draco.focus.w3 == draco.focus.w4 == 0.25


def test_draco_accepts_five_tuple_focus_weights() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    draco = Draco(
        shopfloor=sf,
        focus_weights=(0.2, 0.2, 0.2, 0.2, 0.2),
        wip_target=10,
        loop_target=5,
    )
    assert draco.focus.w5 == pytest.approx(0.2)


def test_draco_rejects_invalid_focus_weights() -> None:
    """Validation runs in Focus.__init__ — DRACO surfaces the error."""
    env = Environment()
    sf = ShopFloor(env=env)
    with pytest.raises(ValueError, match="5 elements"):
        Draco(shopfloor=sf, focus_weights=(0.5, 0.5), wip_target=10, loop_target=5)  # type: ignore[arg-type]


def test_draco_beta_only_focus_weights_runs_without_error() -> None:
    """End-to-end smoke test: DRACO with beta-only FOCUS dispatch (w5=1) runs cleanly."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    draco = Draco(
        shopfloor=sf,
        psp=psp,
        focus_weights=(0.0, 0.0, 0.0, 0.0, 1.0),
        wip_target=10,
        loop_target=5,
    )
    env.process(on_completion_trigger(sf, psp, draco.decide_next_job))
    policy = _policy_factory(draco)

    # A mix of jobs across both servers — DRACO + beta-only FOCUS should
    # complete without raising and dispatch every job.
    j_a = ProductionJob(
        env=env, sku="A", servers=[s1, s2], processing_times=[3.0, 4.0], due_date=100.0, priority_policy=policy
    )
    j_b = ProductionJob(
        env=env, sku="A", servers=[s2, s1], processing_times=[5.0, 2.0], due_date=100.0, priority_policy=policy
    )
    sf.add(j_a)
    sf.add(j_b)
    env.run()

    assert j_a.servers_exit_at[s2] is not None
    assert j_b.servers_exit_at[s1] is not None


# ----- priority_policy: liveness (ctx rebuilt per call / per server) -----


def test_draco_priority_policy_rebuilds_ctx_per_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Draco.priority_policy rebuilds ctx on every call — no stale-closure leak.

    Main's :meth:`~simulatte.server.Server.sort_queue` calls
    ``job.priority_policy(job, server)`` before every dispatch. When a job
    travels through a multi-server routing, ``priority_policy`` is invoked at
    each server in turn. Each call must build a fresh FocusContext — not reuse
    a snapshot frozen at an earlier instant.

    Regression guard: if ``_refresh_queue`` were reintroduced and its stale
    closure assigned to ``job.priority_policy``, the closure would call
    ``Focus.score`` with the pre-baked ctx instead of rebuilding it, and
    ``build_context`` would not be called at dispatch time at all.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    draco = Draco(shopfloor=sf, wip_target=10, loop_target=5)

    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=50.0)
    sf.add(job)

    call_count = 0
    real_build = Focus.build_context

    def counting_build(shopfloor, now, *, psp=None):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return real_build(shopfloor, now, psp=psp)

    monkeypatch.setattr(Focus, "build_context", staticmethod(counting_build))

    draco.priority_policy(job, s1)
    draco.priority_policy(job, s2)

    assert call_count == 2, (
        f"Expected build_context to be called twice (once per server), got {call_count}. "
        "A stale-closure regression (e.g. reintroduced _refresh_queue) would produce "
        "call_count == 0 (closure calls Focus.score directly with frozen ctx)."
    )
