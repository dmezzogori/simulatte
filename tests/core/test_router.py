from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.router import Router
from simulatte.server import Server
from simulatte.psp import PreShopPool


def test_generate_job_adds_to_psp_and_sets_attributes() -> None:
    def inter_arrival() -> float:
        return 1.0

    family_dist = {"A": 1.0}

    def service_val() -> float:
        return 2.0

    def wait_val() -> float:
        return 3.0

    server = Server(capacity=1)
    psp = PreShopPool(check_timeout=100, psp_release_policy=None)

    Router(
        servers=[server],
        psp=psp,
        inter_arrival_distribution=inter_arrival,
        family_distributions=family_dist,
        family_routings={"A": lambda: [server]},
        family_service_times={"A": {server: service_val}},
        waiting_time_distribution={"A": wait_val},
    )

    env = Environment()
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
