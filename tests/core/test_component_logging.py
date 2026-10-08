"""Tests for component-level logging integration."""

from __future__ import annotations

from simulatte.environment import Environment
from simulatte.events import Event
from simulatte.job import ProductionJob
from simulatte.logger import SimLogger
from simulatte.server import JobGranted, JobQueued, JobReleased, Server
from simulatte.shopfloor import ShopFloor


class TestServerEvents:
    """Server resource events (they replace the Server debug log messages)."""

    @staticmethod
    def _run(until: float) -> tuple[Server, ProductionJob, list[Event]]:
        env = Environment(debug=True)
        seen: list[Event] = []
        env.bus.subscribe(seen.append, (JobQueued, JobGranted, JobReleased))
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(
            env=env,
            sku="A",
            servers=[server],
            processing_times=[5.0],
            due_date=100.0,
        )

        sf.add(job)
        env.run(until=until)
        return server, job, seen

    def test_server_emits_queue_entry(self) -> None:
        server, job, seen = self._run(until=1)

        queued = [e for e in seen if isinstance(e, JobQueued)]
        assert len(queued) == 1
        event = queued[0]
        assert event.job == job.id
        assert event.server == server.id
        assert event.queue_length == 1
        assert event.priority == 0.0

    def test_server_grants_and_processes(self) -> None:
        server, job, seen = self._run(until=3)

        granted = [e for e in seen if isinstance(e, JobGranted)]
        assert [(e.job, e.server, e.t) for e in granted] == [(job.id, server.id, 0)]
        assert server.current_jobs == (job,)

        server.env.run(until=10)
        assert server.worked_time == 5.0

    def test_server_emits_job_released(self) -> None:
        server, job, seen = self._run(until=10)

        released = [e for e in seen if isinstance(e, JobReleased)]
        assert len(released) == 1
        event = released[0]
        assert event.job == job.id
        assert event.server == server.id
        granted = next(e for e in seen if isinstance(e, JobGranted))
        assert event.t - granted.t == 5.0  # time at server


class TestShopFloorLogging:
    """Tests for ShopFloor component logging."""

    def test_shopfloor_logs_job_entry(self) -> None:
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")
            env = Environment()
            sf = ShopFloor(env=env)
            server = Server(env=env, capacity=1, shopfloor=sf)
            job = ProductionJob(
                env=env,
                sku="A",
                servers=[server],
                processing_times=[5.0],
                due_date=100.0,
            )

            sf.add(job)

            events = env.log_history.query(component="ShopFloor")
            entry_events = [e for e in events if "entered shopfloor" in e.message]

            assert len(entry_events) == 1
            event = entry_events[0]
            assert event.extra["job_id"] == job.id
            assert event.extra["sku"] == "A"
            assert "wip_total" in event.extra
            assert "jobs_count" in event.extra
        finally:
            SimLogger.set_level(original_level)
            env.close()

    def test_shopfloor_logs_job_finished(self) -> None:
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")
            env = Environment()
            sf = ShopFloor(env=env)
            server = Server(env=env, capacity=1, shopfloor=sf)
            job = ProductionJob(
                env=env,
                sku="A",
                servers=[server],
                processing_times=[5.0],
                due_date=100.0,
            )

            sf.add(job)
            env.run(until=10)

            events = env.log_history.query(component="ShopFloor")
            finished_events = [e for e in events if "finished" in e.message]

            assert len(finished_events) == 1
            event = finished_events[0]
            assert event.extra["job_id"] == job.id
            assert "makespan" in event.extra
            assert "lateness" in event.extra
            assert "total_queue_time" in event.extra
        finally:
            SimLogger.set_level(original_level)
            env.close()

    def test_shopfloor_logs_operations(self) -> None:
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")
            env = Environment()
            sf = ShopFloor(env=env)
            server1 = Server(env=env, capacity=1, shopfloor=sf)
            server2 = Server(env=env, capacity=1, shopfloor=sf)
            job = ProductionJob(
                env=env,
                sku="A",
                servers=[server1, server2],
                processing_times=[3.0, 2.0],
                due_date=100.0,
            )

            sf.add(job)
            env.run(until=10)

            events = env.log_history.query(component="ShopFloor")
            queued_events = [e for e in events if "queued at server" in e.message]
            completed_events = [e for e in events if "completed op" in e.message]

            assert len(queued_events) == 2
            assert len(completed_events) == 2

            # Check op_index is logged
            assert queued_events[0].extra["op_index"] == 0
            assert queued_events[1].extra["op_index"] == 1
        finally:
            SimLogger.set_level(original_level)
            env.close()


class TestLoggingFiltering:
    """Tests for logging level filtering."""

    def test_debug_logs_filtered_at_info_level(self) -> None:
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("INFO")
            env = Environment()
            sf = ShopFloor(env=env)
            server = Server(env=env, capacity=1, shopfloor=sf)
            job = ProductionJob(
                env=env,
                sku="A",
                servers=[server],
                processing_times=[5.0],
                due_date=100.0,
            )

            sf.add(job)
            env.run(until=10)

            # All component logs are at DEBUG level, so should be filtered
            events = list(env.log_history)
            assert len(events) == 0
        finally:
            SimLogger.set_level(original_level)
            env.close()


class TestIntegrationLogging:
    """Integration tests for logging across multiple components."""

    def test_job_lifecycle_logging(self) -> None:
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")
            env = Environment()
            server_events: list[Event] = []
            env.bus.subscribe(server_events.append, (JobQueued, JobGranted, JobReleased))
            sf = ShopFloor(env=env)
            server = Server(env=env, capacity=1, shopfloor=sf)
            job = ProductionJob(
                env=env,
                sku="A",
                servers=[server],
                processing_times=[5.0],
                due_date=100.0,
            )

            sf.add(job)
            env.run(until=10)

            # ShopFloor still logs; the server reports through events
            shopfloor_events = env.log_history.query(component="ShopFloor")

            assert len(shopfloor_events) >= 3  # entry, queued, completed, finished
            assert [e.type_name for e in server_events] == ["job.queued", "job.granted", "job.released"]

            # Verify events are in chronological order
            all_events = list(env.log_history)
            timestamps = [e.timestamp for e in all_events]
            assert timestamps == sorted(timestamps)
        finally:
            SimLogger.set_level(original_level)
            env.close()

    def test_multiple_jobs_logging(self) -> None:
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")
            env = Environment()
            sf = ShopFloor(env=env)
            server = Server(env=env, capacity=1, shopfloor=sf)

            jobs = []
            for i in range(3):
                job = ProductionJob(
                    env=env,
                    sku=f"SKU{i}",
                    servers=[server],
                    processing_times=[2.0],
                    due_date=100.0,
                )
                jobs.append(job)
                sf.add(job)

            env.run(until=20)

            # Verify all jobs have finished events
            finished_events = [e for e in env.log_history.query(component="ShopFloor") if "finished" in e.message]
            assert len(finished_events) == 3

            # Verify each job has its own events
            for job in jobs:
                job_events = [e for e in env.log_history if e.extra.get("job_id") == job.id]
                assert len(job_events) >= 2  # At minimum: entry and finish
        finally:
            SimLogger.set_level(original_level)
            env.close()


def test_job_messages_carry_full_job_ids() -> None:
    """Log messages show the whole job id, so job-10000 never reads as job-1000."""
    original_level = SimLogger.get_level()
    env = Environment()
    try:
        SimLogger.set_level("DEBUG")
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        for _ in range(10_000):
            ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
        assert job.id == "job-10000"

        sf.add(job)
        env.run()

        messages = [e.message for e in env.log_history if e.extra.get("job_id") == job.id]
        assert len(messages) >= 4  # the ShopFloor messages; the server reports through events
        assert all(m.startswith("Job job-10000 ") for m in messages), messages
    finally:
        SimLogger.set_level(original_level)
        env.close()
