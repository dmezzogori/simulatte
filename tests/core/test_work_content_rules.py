"""Tests for work-content dispatching rules in ``simulatte.dispatching_rules.work_content``."""

from __future__ import annotations

from simulatte.dispatching_rules import work_in_next_queue
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestWorkInNextQueue:
    """WINQ — Blackstone, Phillips & Hogg (1982)."""

    def test_returns_zero_on_last_operation(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s_a = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="P", servers=[s_a], processing_times=[2.0], due_date=50.0)
        assert work_in_next_queue(job, s_a) == 0.0

    def test_returns_zero_for_unrouted_server(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s_a = Server(env=env, capacity=1, shopfloor=sf)
        s_b = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="P", servers=[s_a], processing_times=[2.0], due_date=50.0)
        assert work_in_next_queue(job, s_b) == 0.0

    def test_returns_zero_when_next_queue_empty(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s_a = Server(env=env, capacity=1, shopfloor=sf)
        s_b = Server(env=env, capacity=1, shopfloor=sf)
        probe = ProductionJob(env=env, sku="P", servers=[s_a, s_b], processing_times=[2.0, 5.0], due_date=50.0)
        assert work_in_next_queue(probe, s_a) == 0.0

    def test_sums_work_in_next_machine_queue(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s_a = Server(env=env, capacity=1, shopfloor=sf)
        s_b = Server(env=env, capacity=1, shopfloor=sf)

        # Block s_b so jobs routed through it pile up in its queue.
        blocker = ProductionJob(env=env, sku="BLOCK", servers=[s_b], processing_times=[100.0], due_date=1000.0)
        sf.add(blocker)
        env.run(until=0.01)

        # Two jobs queue at s_b (work content 3 and 4).
        q1 = ProductionJob(env=env, sku="Q1", servers=[s_b], processing_times=[3.0], due_date=50.0)
        q2 = ProductionJob(env=env, sku="Q2", servers=[s_b], processing_times=[4.0], due_date=50.0)
        sf.add(q1)
        sf.add(q2)
        env.run(until=0.02)

        # Probe currently at s_a, next machine s_b: WINQ = 3 + 4 = 7 (blocker in service is excluded).
        probe = ProductionJob(env=env, sku="P", servers=[s_a, s_b], processing_times=[2.0, 5.0], due_date=50.0)
        assert work_in_next_queue(probe, s_a) == 7.0
