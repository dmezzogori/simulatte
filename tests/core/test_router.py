from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_generate_job_adds_to_psp_and_sets_attributes() -> None:
    def inter_arrival() -> float:
        return 1.0

    family_dist = {"A": 1.0}

    def service_val() -> float:
        return 2.0

    def wait_val() -> float:
        return 3.0

    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf, check_timeout=100, psp_release_policy=None)

    Router(
        env=env,
        shopfloor=sf,
        servers=[server],
        psp=psp,
        inter_arrival_distribution=inter_arrival,
        family_distributions=family_dist,
        family_routings={"A": lambda: [server]},
        family_service_times={"A": {server: service_val}},
        waiting_time_distribution={"A": wait_val},
    )

    env.run(until=2.1)

    assert len(psp) == 2

    for idx, job in enumerate(psp.jobs, start=1):
        assert job.family == "A"
        assert job._servers == [server]
        assert job._processing_times == (2.0,)
        assert job.created_at == pytest.approx(idx * 1.0)
        assert job.due_date == pytest.approx(idx * 1.0 + 3.0)
        assert job.done is False
        assert all(v is None for v in job.servers_entry_at.values())
        assert all(v is None for v in job.servers_exit_at.values())


def test_generate_job_directly_to_shopfloor_when_no_psp() -> None:
    """Jobs should go directly to shopfloor when psp is None (push system)."""

    def inter_arrival() -> float:
        return 1.0

    family_dist = {"A": 1.0}

    def service_val() -> float:
        return 0.5

    def wait_val() -> float:
        return 10.0

    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    Router(
        env=env,
        shopfloor=sf,
        servers=[server],
        psp=None,  # No PSP - jobs go directly to shopfloor
        inter_arrival_distribution=inter_arrival,
        family_distributions=family_dist,
        family_routings={"A": lambda: [server]},
        family_service_times={"A": {server: service_val}},
        waiting_time_distribution={"A": wait_val},
    )

    env.run(until=3.5)

    # Jobs should have been processed directly on shopfloor
    # First job at t=1, processes for 0.5, done at 1.5
    # Second job at t=2, processes for 0.5, done at 2.5
    # Third job at t=3, processes for 0.5, done at 3.5
    assert len(sf.jobs_done) >= 2
