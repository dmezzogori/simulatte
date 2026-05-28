"""Tests for slack / ratio dispatching rules in ``simulatte.dispatching_rules.slack``."""

from __future__ import annotations

import pytest

from simulatte.dispatching_rules import critical_ratio, planned_slack_time, slack_per_remaining_operation
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestCriticalRatio:
    """Critical Ratio — Berry & Rao (1975)."""

    def test_returns_slack_over_remaining_pt(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        # (30 - 0) / (2 + 3) = 6
        assert critical_ratio(job, s1) == 6.0

    def test_uses_dynamic_remaining_processing_time(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert critical_ratio(job, s2) == 6.0
        # Simulate s1 done: remaining_pt = 3 only.
        job.servers_exit_at[s1] = 5.0
        assert critical_ratio(job, s2) == 10.0

    def test_returns_inf_when_no_remaining_processing_time(self) -> None:
        """Defensive: when every operation has been exited, remaining PT is 0 → inf."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[server], processing_times=[2.0], due_date=30.0)
        job.servers_exit_at[server] = 5.0
        assert critical_ratio(job, server) == float("inf")


class TestPlannedSlackTime:
    """Planned Slack Time — Land & Gaalman (1998)."""

    def test_rejects_negative_allowance(self) -> None:
        with pytest.raises(ValueError, match="allowance must be >= 0"):
            planned_slack_time(allowance=-1.0)

    def test_returns_float_value_with_allowance(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
        # At t=0: slack = 20, PST at server = slack - (5 + 2) = 13
        value = planned_slack_time(allowance=2.0)(job, server)
        assert isinstance(value, float)
        assert value == 13.0

    def test_default_allowance_is_zero(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
        # slack = 20, PST = slack - (5 + 0) = 15
        assert planned_slack_time()(job, server) == 15.0

    def test_zero_allowance_at_first_server(self) -> None:
        """pst at s1 (allowance=0, routing [2, 3], d=30, now=0) = 30 - (2+3) = 25."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert planned_slack_time(allowance=0.0)(job, s1) == 25.0

    def test_only_downstream_operations_count(self) -> None:
        """pst at s2 = 30 - 3 = 27 (only the op from s2 onward counts, not the whole routing)."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert planned_slack_time(allowance=0.0)(job, s2) == 27.0

    def test_positive_allowance_adds_k_per_remaining_op(self) -> None:
        """With allowance=1 over routing [2, 3], pst at s1 = 30 - (2+1) - (3+1) = 23."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert planned_slack_time(allowance=1.0)(job, s1) == 23.0

    def test_returns_inf_for_already_exited_server(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
        sf.add(job)
        env.run()
        assert planned_slack_time(allowance=2.0)(job, server) == float("inf")

    def test_returns_inf_for_unrouted_server(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server_a = Server(env=env, capacity=1, shopfloor=sf)
        server_b = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server_a], processing_times=[5.0], due_date=20.0)
        assert planned_slack_time(allowance=2.0)(job, server_b) == float("inf")

    def test_different_allowances_produce_different_values(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)
        assert planned_slack_time(allowance=1.0)(job, server) != planned_slack_time(allowance=5.0)(job, server)

    def test_discriminates_fractional_differences(self) -> None:
        """PST output must distinguish jobs whose PST differs by less than 1.0."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        pst = planned_slack_time(allowance=2.0)
        job_a = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.3)
        job_b = ProductionJob(env=env, sku="B", servers=[server], processing_times=[1.0], due_date=10.7)
        assert pst(job_a, server) < pst(job_b, server)


class TestSlackPerRemainingOperation:
    """Slack per Remaining Operation — see Kanet (1982) for the ratio-rule family."""

    def test_rejects_negative_allowance(self) -> None:
        with pytest.raises(ValueError, match="allowance must be >= 0"):
            slack_per_remaining_operation(allowance=-1.0)

    def test_divides_pst_by_remaining_op_count(self) -> None:
        """pst(s1, k=0) = 25, |R_i| = 2 → sopn = 12.5."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert slack_per_remaining_operation()(job, s1) == 12.5

    def test_returns_inf_for_already_exited_server(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        job.servers_exit_at[s1] = 5.0
        assert slack_per_remaining_operation()(job, s1) == float("inf")

    def test_shrinks_denominator_as_servers_are_exited(self) -> None:
        """When s1 is exited, |R_i| = 1; sopn at s2 = pst(s2) / 1 = 27.0."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        job.servers_exit_at[s1] = 5.0
        assert slack_per_remaining_operation()(job, s2) == 27.0

    def test_positive_allowance_shifts_result(self) -> None:
        """With allowance=1, pst at s1 = 23.0; |R_i| = 2 → sopn = 11.5."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert slack_per_remaining_operation(allowance=1.0)(job, s1) == 11.5

    def test_returns_inf_for_unrouted_server(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server_a = Server(env=env, capacity=1, shopfloor=sf)
        server_b = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server_a], processing_times=[5.0], due_date=20.0)
        assert slack_per_remaining_operation(allowance=2.0)(job, server_b) == float("inf")
