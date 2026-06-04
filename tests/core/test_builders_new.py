from __future__ import annotations

import random

import pytest

from simulatte.builders import (
    build_continuous_release_system,
    build_conwip_system,
    build_general_flow_shop_system,
    build_pure_flow_shop_system,
    build_pure_job_shop_system,
    build_starvation_avoidance_system,
)
from simulatte.environment import Environment


def test_build_conwip_system_runs_and_caps_wip() -> None:
    random.seed(42)
    peak = 0
    with Environment() as env:
        psp, servers, shop_floor, router = build_conwip_system(env, wip_cap=8)

        def _sample_wip():
            nonlocal peak
            while True:
                peak = max(peak, len(shop_floor.jobs))
                yield env.timeout(0.5)

        env.process(_sample_wip())
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    assert len(shop_floor.jobs_done) > 0
    assert peak > 0, "sampler never observed jobs on the floor"
    assert peak <= 8, f"ConWIP exceeded its WIP cap: peak={peak}"


def test_build_continuous_release_system_runs() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_continuous_release_system(env, wl_norm_level=6.0, allowance_factor=2)
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    assert len(shop_floor.jobs_done) > 0


def test_build_starvation_avoidance_system_runs() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_starvation_avoidance_system(env)
        env.run(until=1000.0)

    assert psp is not None
    assert len(servers) == 6
    # Starvation-only release keeps the first server fed, so some jobs finish.
    assert len(shop_floor.jobs_done) > 0


def test_build_pure_job_shop_system_runs_as_push_system() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_pure_job_shop_system(env)
        env.run(until=1000.0)

    assert psp is None  # immediate-release push baseline
    assert len(servers) == 6
    assert len(shop_floor.jobs_done) > 0
    # Pure Job Shop: routing length U[1, M], undirected.
    for job in shop_floor.jobs_done:
        assert 1 <= len(job.servers) <= len(servers)
        assert len(set(job.servers)) == len(job.servers)  # no re-entry


def test_build_general_flow_shop_system_produces_directed_routings() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_general_flow_shop_system(env)
        env.run(until=1000.0)

    assert psp is None
    assert len(servers) == 6
    assert len(shop_floor.jobs_done) > 0
    # General Flow Shop: variable length, but each routing is directed (ascending index).
    saw_partial_routing = False
    for job in shop_floor.jobs_done:
        indices = [servers.index(s) for s in job.servers]
        assert indices == sorted(indices)
        assert len(set(indices)) == len(indices)
        saw_partial_routing = saw_partial_routing or len(indices) < len(servers)
    assert saw_partial_routing  # at least some orders skip stations (length < M)


def test_build_pure_flow_shop_system_visits_all_servers_in_order() -> None:
    random.seed(42)
    with Environment() as env:
        psp, servers, shop_floor, router = build_pure_flow_shop_system(env)
        env.run(until=1000.0)

    assert psp is None
    assert len(servers) == 6
    # Pure Flow Shop is stable only because the arrival rate is derived for E[L]=M;
    # if it naively reused the job-shop rate (1/0.648) the queue would explode.
    assert len(shop_floor.jobs_done) > 0
    for job in shop_floor.jobs_done:
        assert list(job.servers) == list(servers)


def test_build_pure_job_shop_system_with_twk_due_dates() -> None:
    random.seed(42)
    k = 8.74  # FOCUS pure-job-shop allowance factor (6 work centres)
    with Environment() as env:
        psp, servers, shop_floor, router = build_pure_job_shop_system(env, twk_allowance_factor=k)
        env.run(until=1000.0)

    assert len(shop_floor.jobs_done) > 0
    for job in shop_floor.jobs_done:
        expected_due = job.created_at + k * sum(job.processing_times)
        assert job.due_date == pytest.approx(expected_due)
