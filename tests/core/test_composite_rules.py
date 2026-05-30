"""Tests for composite dispatching rules in ``simulatte.dispatching_rules.composite``."""

from __future__ import annotations

import math

import pytest

from simulatte.dispatching_rules import raghu_rajendran
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestRaghuRajendran:
    """RR — Raghu & Rajendran (1993)."""

    def test_rejects_out_of_range_utilization(self) -> None:
        with pytest.raises(ValueError, match=r"utilization must be in \[0, 1\]"):
            raghu_rajendran(utilization=1.5)

    def test_fixed_zero_utilization(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=20.0)
        rule = raghu_rajendran(utilization=0.0)
        # u=0 -> exp(0)=1; p=2; rpt=5; s_slack = 20 - 5 - 0 = 15; winq=0 (s2 queue empty).
        # Z = 1*2 + (15/5)*1*2 + 0 = 2 + 6 = 8.
        assert rule(job, s) == pytest.approx(8.0)

    def test_fixed_nonzero_utilization(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=20.0)
        rule = raghu_rajendran(utilization=0.5)
        # Z = exp(0.5)*2 + (15/5)*exp(-0.5)*2 + 0 = 2*exp(0.5) + 6*exp(-0.5).
        assert rule(job, s) == pytest.approx(2.0 * math.exp(0.5) + 6.0 * math.exp(-0.5))

    def test_negative_raw_slack_lowers_index(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        # due_date=1 -> raw slack s = 1 - 5 - 0 = -4 (negative); Z = 2 + (-4/5)*2 + 0 = 0.4.
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=1.0)
        rule = raghu_rajendran(utilization=0.0)
        assert rule(job, s) == pytest.approx(0.4)

    def test_live_utilization_defaults_to_server_rate(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=20.0)
        rule = raghu_rajendran()  # live: at now=0, server.utilization_rate == 0 -> same as u=0.
        assert rule(job, s) == pytest.approx(8.0)

    def test_includes_work_in_next_queue(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)

        # Block s2 and queue a job there (work content 4) so WINQ(job via s2) = 4.
        blocker = ProductionJob(env=env, sku="BLOCK", servers=[s2], processing_times=[100.0], due_date=1000.0)
        sf.add(blocker)
        env.run(until=0.01)
        q = ProductionJob(env=env, sku="Q", servers=[s2], processing_times=[4.0], due_date=50.0)
        sf.add(q)
        env.run(until=0.02)

        job = ProductionJob(env=env, sku="A", servers=[s, s2], processing_times=[2.0, 3.0], due_date=20.0)
        rule = raghu_rajendran(utilization=0.0)
        # rpt=5; s_slack = 20 - 5 - now; winq = 4; Z = 2 + (s_slack/5)*2 + 4.
        s_slack = 20.0 - 5.0 - env.now
        assert rule(job, s) == pytest.approx(2.0 + (s_slack / 5.0) * 2.0 + 4.0)

    def test_returns_pt_term_when_no_remaining_processing_time(self) -> None:
        """Defensive: a fully-exited job has empty unfinished_routing -> rpt 0; the
        slack/RPT term is skipped, leaving exp(u)*p + winq."""
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[2.0], due_date=20.0)
        job.servers_exit_at[s] = 5.0  # mark the only operation complete -> unfinished_routing == ()
        rule = raghu_rajendran(utilization=0.0)
        # rpt == 0 -> Z = exp(0)*2 + winq(=0, s is last op) = 2.0.
        assert rule(job, s) == pytest.approx(2.0)
