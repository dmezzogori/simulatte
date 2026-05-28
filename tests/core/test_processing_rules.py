"""Tests for processing-time / baseline rules in ``simulatte.dispatching_rules.processing``."""

from __future__ import annotations

from simulatte.dispatching_rules import first_come_first_served, shortest_processing_time
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestShortestProcessingTime:
    """Shortest Processing Time."""

    def test_returns_processing_time_at_server(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[server], processing_times=[2.5], due_date=100.0)
        assert shortest_processing_time(job, server) == 2.5

    def test_distinguishes_two_servers_in_same_routing(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[s1, s2], processing_times=[1.5, 4.0], due_date=100.0)
        assert shortest_processing_time(job, s1) == 1.5
        assert shortest_processing_time(job, s2) == 4.0


class TestFirstComeFirstServed:
    """First Come First Served (returns 0 so the entry-time tiebreak orders the queue)."""

    def test_returns_zero_for_any_job(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="F1", servers=[server], processing_times=[5.0], due_date=100.0)
        assert first_come_first_served(job, server) == 0.0

    def test_two_jobs_processed_in_arrival_order(self) -> None:
        """With FCFS as the priority rule, queued jobs come out in arrival order."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        # Long blocker so the next two pile up in the queue
        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[server],
            processing_times=[100.0],
            due_date=1000.0,
            priority_policy=first_come_first_served,
        )
        sf.add(blocker)
        env.run(until=0.01)

        first = ProductionJob(
            env=env,
            sku="A",
            servers=[server],
            processing_times=[1.0],
            due_date=10.0,
            priority_policy=first_come_first_served,
        )
        sf.add(first)
        env.run(until=0.02)

        second = ProductionJob(
            env=env,
            sku="B",
            servers=[server],
            processing_times=[1.0],
            due_date=10.0,
            priority_policy=first_come_first_served,
        )
        sf.add(second)

        env.run()
        first_exit = first.servers_exit_at[server]
        second_exit = second.servers_exit_at[server]
        assert first_exit is not None and second_exit is not None
        assert first_exit < second_exit
