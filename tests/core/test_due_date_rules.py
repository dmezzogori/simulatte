"""Tests for due-date dispatching rules in ``simulatte.dispatching_rules.due_date``."""

from __future__ import annotations

from simulatte.dispatching_rules import (
    earliest_due_date,
    modified_operational_due_date,
    operational_due_date,
)
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestEarliestDueDate:
    """Earliest Due Date."""

    def test_returns_due_date(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[server], processing_times=[1.0], due_date=42.0)
        assert earliest_due_date(job, server) == 42.0

    def test_is_server_agnostic(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[1.0, 2.0], due_date=42.0)
        assert earliest_due_date(job, s1) == earliest_due_date(job, s2) == 42.0


class TestOperationalDueDate:
    """Operational Due Date — Land, Stevenson & Thürer (2014)."""

    def test_at_entry_distributes_slack_evenly_across_ops(self) -> None:
        """3 servers, t_r=0, d=30 → slack/op = 10, so ODDs are 10, 20, 30."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        s3 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2, s3], processing_times=[2.0, 3.0, 4.0], due_date=30.0)
        assert operational_due_date(job, s1) == 10.0
        assert operational_due_date(job, s2) == 20.0
        assert operational_due_date(job, s3) == 30.0

    def test_uses_psp_exit_as_release_time_when_set(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[server], processing_times=[1.0], due_date=50.0)
        job.psp_exit_at = 10.0
        # t_r = 10, n=1, |R|=1: o = 10 + 1*(50-10)/1 = 50
        assert operational_due_date(job, server) == 50.0

    def test_clamps_negative_slack_to_zero(self) -> None:
        """If due_date < t_r, slack_per_op clamps to 0 → ODD collapses to t_r."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        env.run(until=10.0)
        # Job created at t=10 with due_date=5 (already late at construction)
        job = ProductionJob(env=env, sku="F1", servers=[server], processing_times=[1.0], due_date=5.0)
        # t_r=10, slack_per_op = max(0, (5-10)/1) = 0 → ODD = 10
        assert operational_due_date(job, server) == 10.0

    def test_uses_static_routing_length_after_upstream_completion(self) -> None:
        """Completed upstream ops do not shrink |R_i| for ODD."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        s3 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2, s3], processing_times=[1.0, 1.0, 1.0], due_date=30.0)
        assert operational_due_date(job, s3) == 30.0
        job.servers_exit_at[s1] = 5.0
        assert operational_due_date(job, s3) == 30.0


class TestModifiedOperationalDueDate:
    """Modified Operational Due Date — Baker & Kanet (1983)."""

    def test_returns_odd_when_slack_dominates(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[1.0, 1.0], due_date=100.0)
        # t_r=0, d=100, N=2: o_i1 = 50. now=0, p=1 → now+p = 1. max(50, 1) = 50.
        assert modified_operational_due_date(job, s1) == 50.0

    def test_returns_now_plus_p_when_job_is_late(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        env.run(until=10.0)
        # Job created at t=10 with due_date=5 (negative slack).
        job = ProductionJob(env=env, sku="F1", servers=[s1], processing_times=[3.0], due_date=5.0)
        # t_r=10 → slack_per_op = 0 → ODD = 10. now=10, p=3 → now+p = 13. max(10, 13) = 13.
        assert modified_operational_due_date(job, s1) == 13.0

    def test_uses_static_odd_after_upstream_completion(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        s3 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2, s3], processing_times=[1.0, 1.0, 1.0], due_date=30.0)
        job.servers_exit_at[s1] = 5.0
        assert modified_operational_due_date(job, s3) == 30.0
