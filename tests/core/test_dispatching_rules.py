from __future__ import annotations

import pytest

from simulatte.dispatching_rules import planned_slack_time
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_planned_slack_time_returns_float() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    pst = planned_slack_time(allowance=2.0)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)

    # At t=0: slack = 20, PST at server = slack - (5 + 2) = 13
    value = pst(job, server)
    assert isinstance(value, float)
    assert value == 13.0


def test_planned_slack_time_returns_inf_for_exited_server() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    pst = planned_slack_time(allowance=2.0)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
    sf.add(job)
    env.run()

    assert pst(job, server) == float("inf")


def test_planned_slack_time_returns_inf_for_unrouted_server() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server_a = Server(env=env, capacity=1, shopfloor=sf)
    server_b = Server(env=env, capacity=1, shopfloor=sf)
    pst = planned_slack_time(allowance=2.0)

    job = ProductionJob(env=env, sku="A", servers=[server_a], processing_times=[5.0], due_date=20.0)

    assert pst(job, server_b) == float("inf")


def test_planned_slack_time_different_allowances_produce_different_values() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    pst_1 = planned_slack_time(allowance=1.0)
    pst_5 = planned_slack_time(allowance=5.0)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)

    assert pst_1(job, server) != pst_5(job, server)


def test_planned_slack_time_default_allowance_is_zero() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    pst = planned_slack_time()

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)

    # At t=0: slack = 20, PST at server = slack - (5 + 0) = 15
    assert pst(job, server) == 15.0


def test_planned_slack_time_rejects_negative_allowance() -> None:
    with pytest.raises(ValueError, match="allowance must be >= 0"):
        planned_slack_time(allowance=-1.0)


def test_planned_slack_time_discriminates_fractional_differences() -> None:
    """PST output must distinguish jobs whose PST differs by less than 1.0."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    pst = planned_slack_time(allowance=2.0)

    job_a = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.3)
    job_b = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1.0], due_date=10.7)

    pst_a = pst(job_a, server)
    pst_b = pst(job_b, server)
    assert pst_a < pst_b
