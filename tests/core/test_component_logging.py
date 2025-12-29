"""Tests for component-level logging integration."""

from __future__ import annotations

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.logger import SimLogger
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


class TestServerLogging:
    """Tests for Server component logging."""

    def test_server_logs_queue_entry(self) -> None:
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
            env.run(until=1)

            events = env.log_history.query(component="Server")
            queue_events = [e for e in events if "entered queue" in e.message]

            assert len(queue_events) >= 1
            event = queue_events[0]
            assert event.extra["job_id"] == job.id
            assert event.extra["server_id"] == server._idx
            assert "queue_length" in event.extra
            assert "priority" in event.extra
        finally:
            SimLogger.set_level(original_level)
            env.close()

    def test_server_logs_processing_started(self) -> None:
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
            env.run(until=3)

            events = env.log_history.query(component="Server")
            processing_events = [e for e in events if "processing started" in e.message]

            assert len(processing_events) >= 1
            event = processing_events[0]
            assert event.extra["job_id"] == job.id
            assert event.extra["processing_time"] == 5.0
        finally:
            SimLogger.set_level(original_level)
            env.close()

    def test_server_logs_job_released(self) -> None:
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

            events = env.log_history.query(component="Server")
            release_events = [e for e in events if "released" in e.message]

            assert len(release_events) >= 1
            event = release_events[0]
            assert event.extra["job_id"] == job.id
            assert "time_at_server" in event.extra
        finally:
            SimLogger.set_level(original_level)
            env.close()


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

            # Verify we have events from both components
            shopfloor_events = env.log_history.query(component="ShopFloor")
            server_events = env.log_history.query(component="Server")

            assert len(shopfloor_events) >= 3  # entry, queued, completed, finished
            assert len(server_events) >= 3  # queue entry, processing, release

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
