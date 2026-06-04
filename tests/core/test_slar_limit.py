from __future__ import annotations

import math
from unittest.mock import Mock

import pytest

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.slar_limit import SlarLimit
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def _make_corrected_system() -> tuple[Environment, ShopFloor, PreShopPool]:
    env = Environment()
    sf = ShopFloor(env=env)
    psp = PreShopPool(env=env, shopfloor=sf)
    return env, sf, psp


# ---------- Constructor validation ----------


def test_slar_limit_rejects_empty_norms() -> None:
    _, sf, psp = _make_corrected_system()
    with pytest.raises(ValueError, match="wl_norm must not be empty"):
        SlarLimit(shopfloor=sf, psp=psp, router=Mock(), wl_norm={})


def test_slar_limit_rejects_non_positive_norm() -> None:
    env, sf, psp = _make_corrected_system()
    server = Server(env=env, capacity=1, shopfloor=sf)
    with pytest.raises(ValueError, match="must be positive and finite"):
        SlarLimit(shopfloor=sf, psp=psp, router=Mock(), wl_norm={server: 0.0})


def test_slar_limit_rejects_negative_norm() -> None:
    env, sf, psp = _make_corrected_system()
    server = Server(env=env, capacity=1, shopfloor=sf)
    with pytest.raises(ValueError, match="must be positive and finite"):
        SlarLimit(shopfloor=sf, psp=psp, router=Mock(), wl_norm={server: -1.0})


def test_slar_limit_rejects_infinite_norm() -> None:
    env, sf, psp = _make_corrected_system()
    server = Server(env=env, capacity=1, shopfloor=sf)
    with pytest.raises(ValueError, match="must be positive and finite"):
        SlarLimit(shopfloor=sf, psp=psp, router=Mock(), wl_norm={server: math.inf})


def test_slar_limit_scalar_norm_expands_to_all_servers() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)
    sl = SlarLimit(shopfloor=sf, psp=psp, router=Mock(), wl_norm=5.0, allowance_factor=3.0)
    assert sl.wl_norm == {s1: 5.0, s2: 5.0}


def test_slar_limit_rejects_missing_server() -> None:
    """Construction raises ValueError when a shopfloor server is missing from wl_norm."""
    env, sf, psp = _make_corrected_system()
    server_a = Server(env=env, capacity=1, shopfloor=sf)
    Server(env=env, capacity=1, shopfloor=sf)  # registers with sf; missing from wl_norm

    with pytest.raises(ValueError, match="missing norms"):
        SlarLimit(
            shopfloor=sf,
            psp=psp,
            router=Mock(),
            wl_norm={server_a: 5.0},
            allowance_factor=2,
        )


def test_slar_limit_constructs_when_all_servers_have_norms() -> None:
    """Construction succeeds when CorrectedWIPStrategy is active and all servers have norms."""
    env, sf, psp = _make_corrected_system()
    server = Server(env=env, capacity=1, shopfloor=sf)

    SlarLimit(
        shopfloor=sf,
        psp=psp,
        router=Mock(),
        wl_norm={server: 5.0},
        allowance_factor=2,
    )


# ---------- Branch 2 urgent-in-queue guard ----------


def test_slar_limit_urgent_insertion_skipped_when_urgent_job_in_queue() -> None:
    """The urgent-insertion branch returns without releasing when the queue already
    contains an urgent job.

    Mirrors test_slar_no_release_when_urgent_job_in_queue from test_slar.py.
    When at least one queued job has a negative PST, priority dispatches it next
    so no PSP release is needed.
    """
    env, sf, psp = _make_corrected_system()
    server = Server(env=env, capacity=1, shopfloor=sf)
    SlarLimit(
        shopfloor=sf,
        psp=psp,
        router=Mock(),
        wl_norm={server: 10.0},
        allowance_factor=2,
    )

    # Processing job (slow, non-urgent)
    job_proc = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=1000.0)
    # Two queued jobs: one urgent (negative PST), one non-urgent
    job_urgent_q = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=-10.0)
    job_normal_q = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=1000.0)
    sf.add(job_proc)
    sf.add(job_urgent_q)
    sf.add(job_normal_q)

    # Advance env so server is busy when psp.add fires on_arrival(starvation_avoidance).
    env.run(until=0.01)

    # PSP candidate that would be urgent (negative PST) — released only if branch fires.
    psp_candidate = ProductionJob(env=env, sku="A", servers=[server], processing_times=[0.5], due_date=-5.0)
    psp.add(psp_candidate)

    # Run past job_proc completion at t=2. Queue then has 2 jobs (one urgent) →
    # urgent-insertion guard returns False; no PSP release.
    env.run(until=3)

    assert psp_candidate in list(psp.jobs)


# ---------- High-norm equivalence with SLAR ----------


def test_slar_limit_high_norm_matches_slar_branch2_selection() -> None:
    """With effectively unbounded norms, SLAR-Limit picks the same urgent-insertion
    candidate as SLAR.

    Mirrors the queue layout from tests/core/test_slar.py::test_slar_negative_pst_release:
    capacity=2, two slow processing jobs + three fast non-urgent queued jobs. When one
    slow job finishes, queue=2 (non-urgent) → urgent-insertion evaluates. Two urgent PSP
    candidates with different SPTs exist; classic SLAR picks the smaller-SPT one.
    With huge norms, SLAR-Limit's iterate-and-accept-first also picks the smaller-SPT one.
    """
    env, sf, psp = _make_corrected_system()
    server = Server(env=env, capacity=2, shopfloor=sf)
    SlarLimit(
        shopfloor=sf,
        psp=psp,
        router=Mock(),
        wl_norm={server: 1e9},
        allowance_factor=2,
    )

    sf.add(ProductionJob(env=env, sku="P", servers=[server], processing_times=[5.0], due_date=1000.0))
    sf.add(ProductionJob(env=env, sku="P", servers=[server], processing_times=[5.0], due_date=1000.0))
    sf.add(ProductionJob(env=env, sku="Q", servers=[server], processing_times=[1.0], due_date=1000.0))
    sf.add(ProductionJob(env=env, sku="Q", servers=[server], processing_times=[1.0], due_date=1000.0))
    sf.add(ProductionJob(env=env, sku="Q", servers=[server], processing_times=[1.0], due_date=1000.0))

    env.run(until=0.1)
    assert len(server.queue) == 3

    urgent_small_spt = ProductionJob(
        env=env,
        sku="US",
        servers=[server],
        processing_times=[0.5],
        due_date=env.now - 10.0,
    )
    urgent_large_spt = ProductionJob(
        env=env,
        sku="UL",
        servers=[server],
        processing_times=[2.0],
        due_date=env.now - 10.0,
    )
    psp.add(urgent_small_spt)
    psp.add(urgent_large_spt)

    env.run(until=6)

    # Iterate by SPT under huge norms: smaller-SPT released first.
    assert urgent_small_spt not in psp.jobs
    assert urgent_large_spt in psp.jobs


# ---------- Branch 2 norm filtering ----------


def test_slar_limit_branch2_norm_filter_releases_larger_spt() -> None:
    """The smaller-SPT urgent candidate exceeds a downstream norm and is skipped;
    the larger-SPT urgent candidate fits all norms and is released.

    Two-server routings:
      - urgent_small_spt: PT=[0.5, 100.0] → contribution at server_b = 100/2 = 50 > norm[B]=10
      - urgent_large_spt: PT=[3.0, 1.0]   → contribution at server_b = 1/2 = 0.5 ≤ 10
    """
    env, sf, psp = _make_corrected_system()
    server_a = Server(env=env, capacity=1, shopfloor=sf)
    server_b = Server(env=env, capacity=1, shopfloor=sf)
    SlarLimit(
        shopfloor=sf,
        psp=psp,
        router=Mock(),
        wl_norm={server_a: 1e9, server_b: 10.0},
        allowance_factor=2,
    )

    # 1 processing + 3 queued, all non-urgent, single-server routing at server_a only.
    # Block server_b so starvation_avoidance doesn't release the two-server PSP
    # candidates on arrival.
    # When the processing job finishes (t=2), one queued moves to processing → queue=2.
    sf.add(ProductionJob(env=env, sku="P", servers=[server_a], processing_times=[2.0], due_date=1000.0))
    sf.add(ProductionJob(env=env, sku="Q", servers=[server_a], processing_times=[1.0], due_date=1000.0))
    sf.add(ProductionJob(env=env, sku="Q", servers=[server_a], processing_times=[1.0], due_date=1000.0))
    sf.add(ProductionJob(env=env, sku="Q", servers=[server_a], processing_times=[1.0], due_date=1000.0))
    env.run(until=0.01)

    urgent_small_spt = ProductionJob(
        env=env,
        sku="US",
        servers=[server_a, server_b],
        processing_times=[0.5, 100.0],
        due_date=-10.0,
    )
    urgent_large_spt = ProductionJob(
        env=env,
        sku="UL",
        servers=[server_a, server_b],
        processing_times=[3.0, 1.0],
        due_date=-10.0,
    )
    psp.add(urgent_small_spt)
    psp.add(urgent_large_spt)

    env.run(until=3)

    # Iterate by SPT (small first): small_spt fails norm[B]; large_spt fits → released.
    assert urgent_small_spt in psp.jobs
    assert urgent_large_spt not in psp.jobs


# ---------- Branch 2 no-fit fallthrough to Branch 3 ----------


def test_slar_limit_branch2_no_fit_falls_to_branch3_postponed_release() -> None:
    """When no urgent PSP candidate fits the norms, the urgent-insertion branch
    returns False and the drain-safety-net fires.

    Setup: queue == 1 at trigger (1 processing + 1 queued; at completion the
    trigger callback runs while the resource is still held by the finishing
    job, so the queued job has not yet been granted the server — see
    ShopFloor.main, where job_processing_end succeeds before the `with` block
    exits). Only urgent candidate exceeds norm[B]; the drain-safety-net still
    releases it via the postponed mechanism (no norm check on that branch).
    """
    env, sf, psp = _make_corrected_system()
    server_a = Server(env=env, capacity=1, shopfloor=sf)
    server_b = Server(env=env, capacity=1, shopfloor=sf)
    SlarLimit(
        shopfloor=sf,
        psp=psp,
        router=Mock(),
        wl_norm={server_a: 1e9, server_b: 1.0},
        allowance_factor=2,
    )

    sf.add(ProductionJob(env=env, sku="P", servers=[server_a], processing_times=[2.0], due_date=1000.0))
    sf.add(ProductionJob(env=env, sku="Q", servers=[server_a], processing_times=[1.0], due_date=1000.0))
    env.run(until=0.01)

    # Single urgent PSP candidate that exceeds norm[B] (contribution = 100/2 = 50 > 1.0).
    urgent_too_big = ProductionJob(
        env=env,
        sku="U",
        servers=[server_a, server_b],
        processing_times=[0.5, 100.0],
        due_date=-10.0,
    )
    psp.add(urgent_too_big)

    # Just past completion (t=2), before the 0.001 postponed-release timeout.
    env.run(until=2.0005)
    assert urgent_too_big not in psp.jobs  # removed from PSP at start of postponed release
    assert urgent_too_big not in sf.jobs  # not yet added (timeout pending)

    # After the postponed delay, the job is on the shopfloor.
    env.run(until=2.1)
    assert urgent_too_big in sf.jobs


# ---------- Branches 1 and 3 ignore norms ----------


def test_slar_limit_branch1_releases_regardless_of_norms() -> None:
    """Branch 1 (empty queue) inherits unchanged from Slar — no norm check applied,
    even if the released job's corrected contribution would exceed a norm.
    """
    env, sf, psp = _make_corrected_system()
    server_a = Server(env=env, capacity=1, shopfloor=sf)
    server_b = Server(env=env, capacity=1, shopfloor=sf)
    SlarLimit(
        shopfloor=sf,
        psp=psp,
        router=Mock(),
        wl_norm={server_a: 1e9, server_b: 1.0},
        allowance_factor=2,
    )

    sf.add(ProductionJob(env=env, sku="P", servers=[server_a], processing_times=[1.0], due_date=1000.0))

    # PSP candidate whose routing at server_b would exceed norm[B] under Branch 2,
    # but Branch 1 fires when queue is empty and has no norm check.
    big_psp = ProductionJob(
        env=env,
        sku="B",
        servers=[server_a, server_b],
        processing_times=[1.0, 100.0],
        due_date=20.0,
    )
    psp.add(big_psp)

    env.run(until=2)

    assert big_psp not in psp.jobs
