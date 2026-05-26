"""Tests for Tier-2 dispatching rules in :mod:`simulatte.dispatching_rules.parametrized`."""

from __future__ import annotations

import pytest

from simulatte.dispatching_rules import Pst, Sopn
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestPst:
    """Planned Slack Time — Land & Gaalman (1998)."""

    def test_pst_rejects_negative_allowance(self) -> None:
        with pytest.raises(ValueError, match="allowance"):
            Pst(allowance=-1.0)

    def test_pst_default_allowance_is_zero(self) -> None:
        assert Pst().allowance == 0.0

    def test_pst_zero_allowance_at_first_server(self) -> None:
        """pst at s1 (with allowance=0, routing [2, 3], d=30, now=0) = 30 - (2+3) = 25."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert Pst(allowance=0.0)(job, s1) == 25.0

    def test_pst_zero_allowance_at_second_server(self) -> None:
        """pst at s2 = 30 - 3 = 27 (only downstream-from-s2 op counts)."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert Pst(allowance=0.0)(job, s2) == 27.0

    def test_pst_positive_allowance_adds_k_per_remaining_op(self) -> None:
        """With allowance=1 over routing [2, 3], pst at s1 = 30 - (2+1) - (3+1) = 23."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert Pst(allowance=1.0)(job, s1) == 23.0

    def test_pst_returns_inf_for_already_exited_server(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        job.servers_exit_at[s1] = 5.0
        assert Pst()(job, s1) == float("inf")


class TestSopn:
    """Slack per Operation — see Kanet (1982) for the ratio-rule family."""

    def test_sopn_rejects_negative_allowance(self) -> None:
        with pytest.raises(ValueError, match="allowance"):
            Sopn(allowance=-1.0)

    def test_sopn_default_allowance_is_zero(self) -> None:
        assert Sopn().allowance == 0.0

    def test_sopn_divides_pst_by_remaining_op_count(self) -> None:
        """pst(s1, k=0) = 25, |R_i| = 2 → sopn = 12.5."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert Sopn()(job, s1) == 12.5

    def test_sopn_returns_inf_for_already_exited_server(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        job.servers_exit_at[s1] = 5.0
        assert Sopn()(job, s1) == float("inf")

    def test_sopn_shrinks_denominator_as_servers_are_exited(self) -> None:
        """When s1 is exited, |R_i| = 1; sopn at s2 = pst(s2) / 1 = 27.0."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        job.servers_exit_at[s1] = 5.0
        assert Sopn()(job, s2) == 27.0

    def test_sopn_positive_allowance_shifts_result(self) -> None:
        """With allowance=1, pst at s1 = 23.0; |R_i| = 2 → sopn = 11.5."""
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[2.0, 3.0], due_date=30.0)
        assert Sopn(allowance=1.0)(job, s1) == 11.5
