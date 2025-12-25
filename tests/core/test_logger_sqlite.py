"""Tests for SQLite storage in the logging module."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from simulatte.environment import Environment
from simulatte.logger import (
    LogEvent,
    SimLogger,
    SQLiteEventStore,
)


# =============================================================================
# Tests for SQLiteEventStore
# =============================================================================


class TestSQLiteEventStore:
    """Unit tests for SQLiteEventStore."""

    def test_create_database_and_schema(self, tmp_path: Path) -> None:
        """Database file and tables created on init."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        assert db_path.exists()

        # Verify schema
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='log_events'")
        assert cursor.fetchone() is not None
        conn.close()
        store.close()

    def test_insert_and_query_single_event(self, tmp_path: Path) -> None:
        """Insert event and retrieve it."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        event = LogEvent(
            timestamp=100.5,
            level="INFO",
            message="Test message",
            component="Server",
            extra={"job_id": "abc123"},
        )
        store.insert("env1", event, "2024-01-01T12:00:00Z")

        results = store.query("env1")
        assert len(results) == 1
        assert results[0].timestamp == 100.5
        assert results[0].level == "INFO"
        assert results[0].message == "Test message"
        assert results[0].component == "Server"
        assert results[0].extra == {"job_id": "abc123"}
        store.close()

    def test_query_by_level(self, tmp_path: Path) -> None:
        """Filter by level works correctly."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        store.insert("env1", LogEvent(0.0, "INFO", "Info msg"), "2024-01-01T12:00:00Z")
        store.insert("env1", LogEvent(1.0, "ERROR", "Error msg"), "2024-01-01T12:00:01Z")
        store.insert("env1", LogEvent(2.0, "INFO", "Info 2"), "2024-01-01T12:00:02Z")

        results = store.query("env1", level="ERROR")
        assert len(results) == 1
        assert results[0].message == "Error msg"
        store.close()

    def test_query_by_level_case_insensitive(self, tmp_path: Path) -> None:
        """Level filter is case-insensitive."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        store.insert("env1", LogEvent(0.0, "INFO", "Info msg"), "2024-01-01T12:00:00Z")

        results = store.query("env1", level="info")
        assert len(results) == 1
        store.close()

    def test_query_by_component(self, tmp_path: Path) -> None:
        """Filter by component works correctly."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        store.insert("env1", LogEvent(0.0, "INFO", "M1", component="Server"), "2024-01-01T12:00:00Z")
        store.insert("env1", LogEvent(1.0, "INFO", "M2", component="Router"), "2024-01-01T12:00:01Z")
        store.insert("env1", LogEvent(2.0, "INFO", "M3", component="Server"), "2024-01-01T12:00:02Z")

        results = store.query("env1", component="Server")
        assert len(results) == 2
        assert results[0].message == "M1"
        assert results[1].message == "M3"
        store.close()

    def test_query_by_time_range(self, tmp_path: Path) -> None:
        """since/until filters work correctly."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        for i in range(5):
            store.insert(
                "env1",
                LogEvent(float(i * 10), "INFO", f"M{i}"),
                f"2024-01-01T12:00:0{i}Z",
            )

        results = store.query("env1", since=15.0, until=35.0)
        assert len(results) == 2
        assert results[0].message == "M2"  # timestamp 20
        assert results[1].message == "M3"  # timestamp 30
        store.close()

    def test_query_with_limit_and_offset(self, tmp_path: Path) -> None:
        """Pagination works correctly."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        for i in range(10):
            store.insert(
                "env1",
                LogEvent(float(i), "INFO", f"M{i}"),
                f"2024-01-01T12:00:0{i}Z",
            )

        # Test limit
        results = store.query("env1", limit=3)
        assert len(results) == 3
        assert results[0].message == "M0"
        assert results[2].message == "M2"

        # Test limit with offset
        results = store.query("env1", limit=3, offset=5)
        assert len(results) == 3
        assert results[0].message == "M5"
        assert results[2].message == "M7"

        # Test offset without limit
        results = store.query("env1", offset=8)
        assert len(results) == 2
        assert results[0].message == "M8"
        assert results[1].message == "M9"
        store.close()

    def test_multi_env_isolation(self, tmp_path: Path) -> None:
        """Events from different env_ids are isolated."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        store.insert("env1", LogEvent(0.0, "INFO", "Env1 msg"), "2024-01-01T12:00:00Z")
        store.insert("env2", LogEvent(0.0, "INFO", "Env2 msg"), "2024-01-01T12:00:00Z")
        store.insert("env1", LogEvent(1.0, "INFO", "Env1 msg2"), "2024-01-01T12:00:01Z")

        results_env1 = store.query("env1")
        results_env2 = store.query("env2")

        assert len(results_env1) == 2
        assert len(results_env2) == 1
        assert results_env1[0].message == "Env1 msg"
        assert results_env2[0].message == "Env2 msg"
        store.close()

    def test_extra_json_serialization(self, tmp_path: Path) -> None:
        """Extra dict is properly serialized/deserialized."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        extra_data = {
            "job_id": "abc123",
            "count": 42,
            "nested": {"key": "value"},
            "list": [1, 2, 3],
        }
        event = LogEvent(0.0, "INFO", "Test", extra=extra_data)
        store.insert("env1", event, "2024-01-01T12:00:00Z")

        results = store.query("env1")
        assert len(results) == 1
        assert results[0].extra == extra_data
        store.close()

    def test_extra_empty_dict(self, tmp_path: Path) -> None:
        """Empty extra dict is handled correctly."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        event = LogEvent(0.0, "INFO", "Test", extra={})
        store.insert("env1", event, "2024-01-01T12:00:00Z")

        results = store.query("env1")
        assert results[0].extra == {}
        store.close()

    def test_execute_sql_raw_query(self, tmp_path: Path) -> None:
        """Raw SQL execution works."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        for i in range(5):
            store.insert(
                "env1",
                LogEvent(float(i), "INFO", f"M{i}"),
                f"2024-01-01T12:00:0{i}Z",
            )

        rows = store.execute_sql("SELECT COUNT(*) as cnt FROM log_events WHERE env_id = ?", ("env1",))
        assert len(rows) == 1
        assert rows[0]["cnt"] == 5
        store.close()

    def test_close_connection(self, tmp_path: Path) -> None:
        """Connection properly closed."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        store.insert("env1", LogEvent(0.0, "INFO", "Test"), "2024-01-01T12:00:00Z")
        store.close()

        # Connection should be closed - verify by checking internal state
        assert store._conn is None

    def test_close_idempotent(self, tmp_path: Path) -> None:
        """Multiple close() calls don't raise."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        store.close()
        store.close()  # Should not raise

    def test_thread_safety(self, tmp_path: Path) -> None:
        """Concurrent writes from threads succeed."""
        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)

        def insert_events(thread_id: int) -> None:
            for i in range(10):
                store.insert(
                    f"env{thread_id}",
                    LogEvent(float(i), "INFO", f"Thread{thread_id}-M{i}"),
                    "2024-01-01T12:00:00Z",
                )

        threads = [threading.Thread(target=insert_events, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify all events were inserted
        total = store.execute_sql("SELECT COUNT(*) as cnt FROM log_events", ())
        assert total[0]["cnt"] == 50  # 5 threads * 10 events
        store.close()


# =============================================================================
# Tests for SimLogger SQLite Integration
# =============================================================================


class TestSimLoggerSQLite:
    """Integration tests for SimLogger with SQLite storage."""

    def test_db_disabled_by_default(self) -> None:
        """SQLite not enabled when db_path not provided."""
        env = Environment()
        try:
            assert not env.logger.db_enabled
        finally:
            env.close()

    def test_db_enabled_with_path(self, tmp_path: Path) -> None:
        """SQLite enabled when db_path provided."""
        db_path = tmp_path / "test.db"
        env = Environment(log_db_path=db_path)
        try:
            assert env.logger.db_enabled
            assert db_path.exists()
        finally:
            env.close()

    def test_log_writes_to_both_buffer_and_db(self, tmp_path: Path) -> None:
        """Events appear in both history buffer and SQLite."""
        db_path = tmp_path / "test.db"
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")
            env = Environment(log_db_path=db_path)
            env.run(until=100)

            env.info("Test message", component="Server", job_id="abc")
            env.error("Error message", component="Router")

            # Check in-memory buffer
            buffer_events = list(env.log_history)
            assert len(buffer_events) == 2

            # Check SQLite
            sql_events = env.logger.query_sql()
            assert len(sql_events) == 2
            assert sql_events[0].message == "Test message"
            assert sql_events[0].component == "Server"
            assert sql_events[0].extra == {"job_id": "abc"}
            assert sql_events[1].message == "Error message"
        finally:
            SimLogger.set_level(original_level)
            env.close()

    def test_query_sql_returns_log_events(self, tmp_path: Path) -> None:
        """query_sql() returns LogEvent objects."""
        db_path = tmp_path / "test.db"
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")
            env = Environment(log_db_path=db_path)

            env.info("Test message")

            results = env.logger.query_sql()
            assert len(results) == 1
            assert isinstance(results[0], LogEvent)
        finally:
            SimLogger.set_level(original_level)
            env.close()

    def test_query_sql_filters(self, tmp_path: Path) -> None:
        """query_sql() supports all filter parameters."""
        db_path = tmp_path / "test.db"
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")
            env = Environment(log_db_path=db_path)

            env.run(until=10)
            env.info("Info 1", component="Server")
            env.run(until=20)
            env.error("Error 1", component="Server")
            env.run(until=30)
            env.info("Info 2", component="Router")

            # Filter by level
            errors = env.logger.query_sql(level="ERROR")
            assert len(errors) == 1
            assert errors[0].message == "Error 1"

            # Filter by component
            server_events = env.logger.query_sql(component="Server")
            assert len(server_events) == 2

            # Filter by time
            time_filtered = env.logger.query_sql(since=15.0, until=25.0)
            assert len(time_filtered) == 1
            assert time_filtered[0].timestamp == 20.0

            # Pagination
            limited = env.logger.query_sql(limit=2)
            assert len(limited) == 2
        finally:
            SimLogger.set_level(original_level)
            env.close()

    def test_query_sql_raises_when_disabled(self) -> None:
        """query_sql() raises RuntimeError when db not enabled."""
        env = Environment()
        try:
            with pytest.raises(RuntimeError, match="SQLite storage not enabled"):
                env.logger.query_sql()
        finally:
            env.close()

    def test_execute_sql_works(self, tmp_path: Path) -> None:
        """execute_sql() returns raw rows."""
        db_path = tmp_path / "test.db"
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")
            env = Environment(log_db_path=db_path)

            env.info("Test 1")
            env.info("Test 2")

            rows = env.logger.execute_sql(
                "SELECT COUNT(*) as cnt FROM log_events WHERE env_id = ?",
                (env.logger.env_id,),
            )
            assert rows[0]["cnt"] == 2
        finally:
            SimLogger.set_level(original_level)
            env.close()

    def test_execute_sql_raises_when_disabled(self) -> None:
        """execute_sql() raises RuntimeError when db not enabled."""
        env = Environment()
        try:
            with pytest.raises(RuntimeError, match="SQLite storage not enabled"):
                env.logger.execute_sql("SELECT 1")
        finally:
            env.close()

    def test_env_id_property(self, tmp_path: Path) -> None:
        """env_id property returns the UUID hex string."""
        db_path = tmp_path / "test.db"
        env = Environment(log_db_path=db_path)
        try:
            env_id = env.logger.env_id
            assert isinstance(env_id, str)
            assert len(env_id) == 32  # UUID hex string length
        finally:
            env.close()

    def test_db_enabled_property(self, tmp_path: Path) -> None:
        """db_enabled property reflects state correctly."""
        # Without db_path
        env1 = Environment()
        assert not env1.logger.db_enabled
        env1.close()

        # With db_path
        db_path = tmp_path / "test.db"
        env2 = Environment(log_db_path=db_path)
        assert env2.logger.db_enabled
        env2.close()

    def test_close_cleans_up_db_connection(self, tmp_path: Path) -> None:
        """close() properly closes SQLite connection."""
        db_path = tmp_path / "test.db"
        env = Environment(log_db_path=db_path)
        env.close()

        # Internal state should show db_store is None
        assert env.logger._db_store is None

    def test_context_manager_closes_db(self, tmp_path: Path) -> None:
        """Environment context manager closes SQLite."""
        db_path = tmp_path / "test.db"
        with Environment(log_db_path=db_path) as env:
            env.info("Test")
            assert env.logger.db_enabled

        # After exiting context, db_store should be None
        assert env.logger._db_store is None


# =============================================================================
# Tests for Multi-Environment / Persistence
# =============================================================================


class TestSQLitePersistence:
    """Tests for data persistence across environments."""

    def test_multiple_envs_shared_db(self, tmp_path: Path) -> None:
        """Multiple environments can share same db file."""
        db_path = tmp_path / "shared.db"
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")

            # First environment
            env1 = Environment(log_db_path=db_path)
            env1_id = env1.logger.env_id
            env1.info("Env1 message")
            env1.close()

            # Second environment - same db
            env2 = Environment(log_db_path=db_path)
            env2_id = env2.logger.env_id
            env2.info("Env2 message")

            # Query for each env_id
            rows = env2.logger.execute_sql("SELECT env_id, message FROM log_events ORDER BY id", ())
            assert len(rows) == 2
            assert rows[0]["env_id"] == env1_id
            assert rows[0]["message"] == "Env1 message"
            assert rows[1]["env_id"] == env2_id
            assert rows[1]["message"] == "Env2 message"

            env2.close()
        finally:
            SimLogger.set_level(original_level)

    def test_sequential_runs_accumulate_data(self, tmp_path: Path) -> None:
        """Data persists across Environment instances."""
        db_path = tmp_path / "persist.db"
        original_level = SimLogger.get_level()
        try:
            SimLogger.set_level("DEBUG")

            # First run
            with Environment(log_db_path=db_path) as env:
                env.info("Run 1 - Message 1")
                env.info("Run 1 - Message 2")

            # Second run - db should still have first run's data
            with Environment(log_db_path=db_path) as env:
                env.info("Run 2 - Message 1")

                # Count all events in database
                rows = env.logger.execute_sql("SELECT COUNT(*) as cnt FROM log_events", ())
                assert rows[0]["cnt"] == 3
        finally:
            SimLogger.set_level(original_level)


# =============================================================================
# Tests for Finalization
# =============================================================================


class TestSQLiteFinalization:
    """Tests for cleanup via finalizers."""

    def test_finalize_logger_handles_both_resources(self, tmp_path: Path) -> None:
        """_finalize_logger cleans up both handler and db_store."""
        from simulatte.logger import _finalize_logger

        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)
        store.insert("env1", LogEvent(0.0, "INFO", "Test"), "2024-01-01T12:00:00Z")

        # Call finalizer directly
        _finalize_logger(None, store)

        # Store should be closed
        assert store._conn is None

    def test_finalize_logger_handles_none_values(self) -> None:
        """_finalize_logger handles None values gracefully."""
        from simulatte.logger import _finalize_logger

        # Should not raise
        _finalize_logger(None, None)

    def test_finalize_logger_handles_already_closed_store(self, tmp_path: Path) -> None:
        """_finalize_logger handles already-closed store."""
        from simulatte.logger import _finalize_logger

        db_path = tmp_path / "test.db"
        store = SQLiteEventStore(db_path)
        store.close()

        # Should not raise even though store is already closed
        _finalize_logger(None, store)
