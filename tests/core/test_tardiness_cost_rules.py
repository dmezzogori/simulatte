"""Tests for tardiness-cost dispatching rules in ``simulatte.dispatching_rules.tardiness_cost``."""

from __future__ import annotations

import math

import pytest

from simulatte.dispatching_rules import apparent_tardiness_cost
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestApparentTardinessCost:
    """ATC — Vepsäläinen & Morton (1987)."""

    def test_rejects_nonpositive_lookahead(self) -> None:
        with pytest.raises(ValueError, match="lookahead must be > 0"):
            apparent_tardiness_cost(lookahead=0.0)

    def test_rejects_nonpositive_avg_processing(self) -> None:
        with pytest.raises(ValueError, match="avg_processing must be > 0"):
            apparent_tardiness_cost(lookahead=2.0, avg_processing=0.0)

    def test_negated_index_with_fixed_avg_processing(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[2.0], due_date=10.0)
        rule = apparent_tardiness_cost(lookahead=2.0, avg_processing=4.0)
        # slack = max(0, 10 - 2 - 0) = 8; I = (1/2)*exp(-8 / (2*4)) = 0.5*exp(-1); return -I.
        assert rule(job, s) == pytest.approx(-(0.5 * math.exp(-1.0)))

    def test_applies_weight_function(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[2.0], due_date=10.0)
        rule = apparent_tardiness_cost(lookahead=2.0, avg_processing=4.0, weight=lambda _job: 3.0)
        # I = (3/2)*exp(-8/(2*4)) = 1.5*exp(-1); return -I.
        assert rule(job, s) == pytest.approx(-(1.5 * math.exp(-1.0)))

    def test_live_pbar_falls_back_to_p_when_queue_empty(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[2.0], due_date=10.0)
        rule = apparent_tardiness_cost(lookahead=2.0)  # live p_bar, empty queue -> p_bar = p = 2
        # slack = 8; I = 0.5*exp(-8 / (2*2)) = 0.5*exp(-2); return -I.
        assert rule(job, s) == pytest.approx(-(0.5 * math.exp(-2.0)))

    def test_live_pbar_uses_queue_mean(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        rule = apparent_tardiness_cost(lookahead=2.0)

        blocker = ProductionJob(env=env, sku="BLOCK", servers=[s], processing_times=[100.0], due_date=1000.0)
        sf.add(blocker)
        env.run(until=0.01)

        # Two jobs queue at s with processing 4 and 6 -> live p_bar = (4+6)/2 = 5.
        j1 = ProductionJob(env=env, sku="J1", servers=[s], processing_times=[4.0], due_date=50.0, priority_policy=rule)
        j2 = ProductionJob(env=env, sku="J2", servers=[s], processing_times=[6.0], due_date=50.0, priority_policy=rule)
        sf.add(j1)
        sf.add(j2)
        env.run(until=0.02)

        # For j1: p=4, p_bar=5, slack = max(0, 50 - 4 - now); I = (1/4)*exp(-slack/(2*5)); return -I.
        slack = max(0.0, 50.0 - 4.0 - env.now)
        assert rule(j1, s) == pytest.approx(-((1 / 4.0) * math.exp(-slack / (2.0 * 5.0))))

    def test_returns_neg_inf_for_zero_processing(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[s], processing_times=[0.0], due_date=10.0)
        rule = apparent_tardiness_cost(lookahead=2.0, avg_processing=4.0)
        assert rule(job, s) == float("-inf")

    def test_live_pbar_falls_back_to_p_when_queue_mean_nonpositive(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s = Server(env=env, capacity=1, shopfloor=sf)
        rule = apparent_tardiness_cost(lookahead=2.0)  # live p_bar

        # Block s, then queue two zero-processing jobs so the live mean is 0 -> p_bar falls back to p.
        blocker = ProductionJob(env=env, sku="BLOCK", servers=[s], processing_times=[100.0], due_date=1000.0)
        sf.add(blocker)
        env.run(until=0.01)
        z1 = ProductionJob(env=env, sku="Z1", servers=[s], processing_times=[0.0], due_date=50.0)
        z2 = ProductionJob(env=env, sku="Z2", servers=[s], processing_times=[0.0], due_date=50.0)
        sf.add(z1)
        sf.add(z2)
        env.run(until=0.02)

        # Probe (not enqueued) has p=4; queue mean is 0 -> p_bar falls back to p=4.
        probe = ProductionJob(env=env, sku="P", servers=[s], processing_times=[4.0], due_date=50.0)
        slack = max(0.0, 50.0 - 4.0 - env.now)
        assert rule(probe, s) == pytest.approx(-((1 / 4.0) * math.exp(-slack / (2.0 * 4.0))))
