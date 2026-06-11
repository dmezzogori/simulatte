from __future__ import annotations

import math
from unittest.mock import Mock

import pytest

from simulatte.dispatching_rules import planned_slack_time
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.lumscor import LumsCor
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import CorrectedWIPStrategy, ShopFloor


def _real_router(env, sf, psp, server) -> Router:
    """A real (non-Mock) Router — required because the PST wiring test reads
    back the attribute LumsCor assigns to ``router.priority_policies`` and
    invokes it.

    Its ``generate_job`` process is effectively inert in these tests: the
    inter-arrival distribution returns a huge value and no test using this
    helper runs the env far enough to fire it. The point is to exercise
    ``LumsCor.__init__``'s real assignment to ``router.priority_policies``.
    """
    return Router(
        env=env,
        shopfloor=sf,
        servers=[server],
        psp=psp,
        inter_arrival_distribution=lambda: 1e9,
        sku_distributions={"A": 1.0},
        sku_routings={"A": lambda: [server]},
        sku_service_times={"A": {server: lambda: 1.0}},
        due_date_offset_distribution={"A": lambda: 30.0},
    )


def _lumscor(env, sf, psp, *, wl_norm, allowance_factor=2, check_timeout=10_000.0) -> LumsCor:
    return LumsCor(
        shopfloor=sf,
        psp=psp,
        router=Mock(),
        wl_norm=wl_norm,
        check_timeout=check_timeout,
        allowance_factor=allowance_factor,
    )


def test_lumscor_scalar_norm_expands_to_all_servers() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    lumscor = _lumscor(env, sf, psp, wl_norm=7.5)
    assert lumscor.wl_norm == {s1: 7.5, s2: 7.5}


# ---------- Constructor validation ----------


def test_lumscor_rejects_empty_norms() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    psp = PreShopPool(env=env, shopfloor=sf)
    with pytest.raises(ValueError, match="wl_norm must not be empty"):
        _lumscor(env, sf, psp, wl_norm={})


def test_lumscor_rejects_scalar_zero_norm() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    with pytest.raises(ValueError, match="must be positive and finite"):
        _lumscor(env, sf, psp, wl_norm=0.0)


def test_lumscor_rejects_scalar_negative_norm() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    with pytest.raises(ValueError, match="must be positive and finite"):
        _lumscor(env, sf, psp, wl_norm=-1.0)


def test_lumscor_rejects_infinite_norm() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    with pytest.raises(ValueError, match="must be positive and finite"):
        _lumscor(env, sf, psp, wl_norm={server: math.inf})


def test_lumscor_rejects_nan_norm() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    with pytest.raises(ValueError, match="must be positive and finite"):
        _lumscor(env, sf, psp, wl_norm={server: math.nan})


def test_lumscor_rejects_missing_server() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server_a = Server(env=env, capacity=1, shopfloor=sf)
    Server(env=env, capacity=1, shopfloor=sf)  # registers with sf; missing from wl_norm
    psp = PreShopPool(env=env, shopfloor=sf)
    with pytest.raises(ValueError, match="missing norms"):
        _lumscor(env, sf, psp, wl_norm={server_a: 5.0})


def test_lumscor_release_under_norm() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # High workload norm allows releases.
    lumscor = _lumscor(env, sf, psp, wl_norm={server: 100.0})

    # Occupy the server so the candidate is not released on arrival by
    # starvation_avoidance; a small blocker keeps WIP well under the norm.
    blocker = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.01)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job)
    assert job in psp.jobs  # not released by starvation_avoidance

    lumscor.periodic_release(psp)

    # Job should be released since WIP is well under norm.
    assert job not in psp.jobs
    assert job in sf.jobs


def test_lumscor_release_respects_norm() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Very low workload norm blocks releases.
    lumscor = _lumscor(env, sf, psp, wl_norm={server: 0.1})

    # Occupy the server so starvation_avoidance doesn't release the candidate on
    # arrival (it ignores the norm). The norm check itself must keep it in PSP.
    blocker = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.01)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    psp.add(job)
    assert job in psp.jobs  # not released by starvation_avoidance

    lumscor.periodic_release(psp)

    # Job should stay in PSP since adding it would exceed norm.
    assert job in psp.jobs


def test_lumscor_release_order_by_planned_release_date() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    lumscor = _lumscor(env, sf, psp, wl_norm={server: 100.0})

    # Occupy the server so the candidates are not released on arrival; a small
    # blocker keeps WIP well under the norm.
    blocker = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.01)

    # Add jobs with different due dates.
    job_late = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=50.0)
    job_early = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)

    psp.add(job_late)
    psp.add(job_early)
    assert job_late in psp.jobs and job_early in psp.jobs  # not released on arrival

    lumscor.periodic_release(psp)

    # Both should be released since norm is high.
    assert job_early not in psp.jobs
    assert job_late not in psp.jobs


def test_lumscor_starvation_release_when_empty() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    _lumscor(env, sf, psp, wl_norm={server: 100.0})

    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)
    env.run(until=0.01)  # server busy -> starvation_avoidance won't grab job2 on arrival

    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=20.0)
    psp.add(job2)
    assert job2 in psp.jobs
    env.run(until=2)
    assert job2 not in psp.jobs


def test_lumscor_starvation_release_when_queue_has_one() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    _lumscor(env, sf, psp, wl_norm={server: 100.0})

    # job1 processes first; job2 waits in queue so has_one fires when job1 finishes.
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=15.0)
    sf.add(job1)
    sf.add(job2)

    # Advance env so the server is busy when psp.add fires on_arrival(starvation_avoidance).
    env.run(until=0.01)

    job3 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=25.0)
    psp.add(job3)
    assert job3 in psp.jobs  # not released by starvation_avoidance

    # At job1 finish (t=2): job3 is removed from PSP immediately but not yet on shopfloor.
    env.run(until=2.0005)
    assert job3 not in psp.jobs
    assert job3 not in sf.jobs

    # After the postponed delay, job3 enters the shopfloor.
    env.run(until=2.1)
    assert job3 in sf.jobs


def test_lumscor_starvation_no_release_when_queue_has_one_no_candidates() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    _lumscor(env, sf, psp, wl_norm={server1: 100.0, server2: 100.0})

    # Two jobs on server1 so has_one fires when job1 finishes (job2 in queue).
    # Block server2 too so starvation_avoidance doesn't release job3 on arrival.
    job1 = ProductionJob(env=env, sku="A", servers=[server1], processing_times=[2.0], due_date=10.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server1], processing_times=[2.0], due_date=15.0)
    blocker = ProductionJob(env=env, sku="C", servers=[server2], processing_times=[100.0], due_date=1000.0)
    sf.add(job1)
    sf.add(job2)
    sf.add(blocker)
    env.run(until=0.01)

    # PSP candidate starts at server2, not server1.
    job3 = ProductionJob(env=env, sku="B", servers=[server2], processing_times=[1.0], due_date=25.0)
    psp.add(job3)
    assert job3 in psp.jobs  # not released by starvation_avoidance (server2 busy)

    env.run(until=3)

    # job3 should remain in PSP (no candidates start at server1).
    assert job3 in psp.jobs


def test_lumscor_starvation_no_release_when_queue_has_many() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    _lumscor(env, sf, psp, wl_norm={server: 100.0})

    # Three jobs on server so queue has 2 when job1 finishes (neither is_empty nor has_one).
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=10.0)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=15.0)
    job3 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=20.0)
    sf.add(job1)
    sf.add(job2)
    sf.add(job3)

    # Advance env so the server is busy when psp.add fires on_arrival(starvation_avoidance).
    env.run(until=0.01)

    psp_job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=30.0)
    psp.add(psp_job)
    assert psp_job in psp.jobs  # not released by starvation_avoidance

    env.run(until=3)

    # No release: server had 2 jobs in queue, not a starvation risk.
    assert psp_job in psp.jobs


def test_lumscor_starvation_no_release_when_no_candidates() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    _lumscor(env, sf, psp, wl_norm={server1: 100.0, server2: 100.0})

    # Add jobs to server1 and server2 so neither is idle when psp.add fires
    # the on_arrival(starvation_avoidance) callback.
    job1 = ProductionJob(env=env, sku="A", servers=[server1], processing_times=[1.0], due_date=10.0)
    blocker = ProductionJob(env=env, sku="C", servers=[server2], processing_times=[100.0], due_date=1000.0)
    sf.add(job1)
    sf.add(blocker)
    env.run(until=0.01)

    # Add candidate to PSP that starts at server2.
    job2 = ProductionJob(env=env, sku="B", servers=[server2], processing_times=[1.0], due_date=20.0)
    psp.add(job2)
    assert job2 in psp.jobs  # not released by starvation_avoidance (server2 busy)

    env.run(until=2)

    # job2 should stay in PSP (starts at different server than job1's completion).
    assert job2 in psp.jobs


def test_lumscor_starvation_selects_by_planned_release_date() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    _lumscor(env, sf, psp, wl_norm={server: 100.0})

    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    sf.add(job1)

    # Advance env so the server is busy when psp.add fires on_arrival(starvation_avoidance);
    # otherwise both candidates would be released on arrival, bypassing the
    # earliest-planned-release-date selection under test.
    env.run(until=0.01)

    # Add two candidates with different due dates.
    job_urgent = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=5.0)
    job_relaxed = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=50.0)
    psp.add(job_urgent)
    psp.add(job_relaxed)
    assert job_urgent in psp.jobs and job_relaxed in psp.jobs  # neither released on arrival

    env.run(until=2)

    # The urgent job (earlier planned release date) should be selected; the
    # relaxed one stays in PSP because the empty-server branch releases one.
    assert job_urgent not in psp.jobs
    assert job_relaxed in psp.jobs


def test_lumscor_starvation_release_no_previous_server() -> None:
    """Starvation release should return early when triggering job has no previous server."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    lumscor = _lumscor(env, sf, psp, wl_norm={server: 100.0})

    # Occupy the server with a long blocker so starvation_avoidance does not
    # release the candidate on arrival. Do not run past the blocker's
    # completion: the auto-wired completion hook would otherwise fire
    # starvation_release and release the candidate.
    blocker = ProductionJob(env=env, sku="B", servers=[server], processing_times=[100.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.01)

    # Add a candidate job to PSP.
    candidate = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=20.0)
    psp.add(candidate)
    assert candidate in psp.jobs  # not released by starvation_avoidance (server busy)

    # Create a fresh job with no previous_server (never processed).
    fresh_job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    assert fresh_job.previous_server is None

    # This should return early without releasing anything.
    lumscor.starvation_release(fresh_job, psp)

    # Candidate should still be in PSP.
    assert candidate in psp.jobs


# ---------- Self-wiring side effects (mutation-pinned) ----------


def test_lumscor_self_wires_corrected_wip_strategy() -> None:
    """LumsCor.__init__ sets CorrectedWIPStrategy on the shopfloor itself.

    No manual ``set_wip_strategy`` is performed here on purpose: this pins the
    self-wiring line in ``LumsCor.__init__``. Deleting that line makes this fail.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    # Sanity: the floor starts on the default (non-corrected) strategy.
    assert not isinstance(sf.wip_strategy, CorrectedWIPStrategy)

    _lumscor(env, sf, psp, wl_norm={server: 5.0})

    assert isinstance(sf.wip_strategy, CorrectedWIPStrategy)


def test_lumscor_self_wires_pst_priority_on_router() -> None:
    """LumsCor.__init__ wires PST (with the given allowance) onto the router.

    Uses a *real* Router and asserts behaviorally: the wired callable must
    reproduce ``planned_slack_time(allowance=allowance_factor)`` exactly.
    An identity-only ('not None'/'changed') check would not catch a
    wrong-allowance mutation; the numeric comparison does.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = _real_router(env, sf, psp, server)

    allowance_factor = 2
    LumsCor(
        shopfloor=sf,
        psp=psp,
        router=router,
        wl_norm={server: 100.0},
        check_timeout=10_000.0,
        allowance_factor=allowance_factor,
    )

    wired = router.priority_policies
    assert wired is not None

    # Single-op job at now=0: pst = (due - now) - (pt + allowance) = 20 - (5 + 2) = 13.
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    expected = planned_slack_time(allowance=float(allowance_factor))(job, server)
    assert wired(job, server) == expected == pytest.approx(13.0)

    # A wrong allowance (e.g. the check_timeout swapped in) would yield a
    # different value, so this comparison fails the mutation.
    assert wired(job, server) != planned_slack_time(allowance=10_000.0)(job, server)


def test_lumscor_self_wires_periodic_trigger() -> None:
    """LumsCor.__init__ starts the periodic release trigger itself.

    Isolation: only the *periodic* path may release the candidate here.
    - The first server is kept busy by a long blocker, so the on-arrival
      ``starvation_avoidance`` cannot release the candidate.
    - The blocker's processing time (100.0) outlasts ``check_timeout`` (5.0),
      so no completion fires before the timeout and ``starvation_release``
      cannot release it either.
    With a generous norm, the only thing that can move the candidate onto the
    floor is the periodic trigger firing at t=check_timeout. Deleting or
    mis-wiring that trigger leaves the candidate stranded in the PSP.
    """
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    check_timeout = 5.0
    _lumscor(env, sf, psp, wl_norm={server: 10_000.0}, check_timeout=check_timeout)

    # Long blocker: busies the server (suppresses on-arrival release) and
    # outlasts check_timeout (suppresses completion-triggered release).
    blocker = ProductionJob(env=env, sku="B", servers=[server], processing_times=[100.0], due_date=1000.0)
    sf.add(blocker)
    env.run(until=0.01)

    candidate = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=50.0)
    psp.add(candidate)
    assert candidate in psp.jobs  # not released on arrival (server busy)

    # Strictly before the timeout: nothing else can release it.
    env.run(until=check_timeout - 1.0)
    assert candidate in psp.jobs
    assert candidate not in sf.jobs

    # Just past the timeout: only the periodic trigger could have released it.
    env.run(until=check_timeout + 1.0)
    assert candidate not in psp.jobs
    assert candidate in sf.jobs
