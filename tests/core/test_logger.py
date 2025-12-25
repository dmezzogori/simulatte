from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from loguru import logger as loguru_logger

from simulatte.environment import Environment
from simulatte.logger import (
    EventHistoryBuffer,
    LogEvent,
    SimLogger,
    _format_sim_time,
)


# =============================================================================
# Tests for _format_sim_time (preserved from original)
# =============================================================================


def test_format_sim_time_zero() -> None:
    result = _format_sim_time(0)
    # When input is int, // returns int, so days=0 formats as "00"
    assert result == "00d 00:00:0.00"


def test_format_sim_time_seconds_only() -> None:
    result = _format_sim_time(45.5)
    # When input is float, // returns float, so days=0.0 formats as "0.0"
    assert result == "0.0d 00:00:45.50"


def test_format_sim_time_minutes() -> None:
    result = _format_sim_time(125.25)  # 2 minutes, 5.25 seconds
    assert result == "0.0d 00:02:5.25"


def test_format_sim_time_hours() -> None:
    result = _format_sim_time(3661.5)  # 1 hour, 1 minute, 1.5 seconds
    assert result == "0.0d 01:01:1.50"


def test_format_sim_time_days() -> None:
    result = _format_sim_time(90061.75)  # 1 day, 1 hour, 1 minute, 1.75 seconds
    assert result == "1.0d 01:01:1.75"


def test_format_sim_time_multiple_days() -> None:
    result = _format_sim_time(259200.0)  # 3 days as float
    assert result == "3.0d 00:00:0.00"


# =============================================================================
# Tests for LogEvent
# =============================================================================


def test_log_event_creation() -> None:
    event = LogEvent(
        timestamp=100.0,
        level="INFO",
        message="Test message",
        component="Server",
        extra={"key": "value"},
    )
    assert event.timestamp == 100.0
    assert event.level == "INFO"
    assert event.message == "Test message"
    assert event.component == "Server"
    assert event.extra == {"key": "value"}


def test_log_event_defaults() -> None:
    event = LogEvent(timestamp=0.0, level="DEBUG", message="Test")
    assert event.component is None
    assert event.extra == {}


def test_log_event_immutable() -> None:
    event = LogEvent(timestamp=0.0, level="INFO", message="Test")
    try:
        event.timestamp = 100.0  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except AttributeError:
        pass  # Expected - frozen dataclass


# =============================================================================
# Tests for EventHistoryBuffer
# =============================================================================


def test_history_buffer_append_and_iterate() -> None:
    buffer = EventHistoryBuffer(max_size=10)
    event1 = LogEvent(timestamp=0.0, level="INFO", message="First")
    event2 = LogEvent(timestamp=1.0, level="DEBUG", message="Second")

    buffer.append(event1)
    buffer.append(event2)

    events = list(buffer)
    assert len(events) == 2
    assert events[0] == event1
    assert events[1] == event2


def test_history_buffer_max_size() -> None:
    buffer = EventHistoryBuffer(max_size=3)

    for i in range(5):
        buffer.append(LogEvent(timestamp=float(i), level="INFO", message=f"Message {i}"))

    assert len(buffer) == 3
    events = list(buffer)
    assert events[0].message == "Message 2"
    assert events[1].message == "Message 3"
    assert events[2].message == "Message 4"


def test_history_buffer_clear() -> None:
    buffer = EventHistoryBuffer(max_size=10)
    buffer.append(LogEvent(timestamp=0.0, level="INFO", message="Test"))
    assert len(buffer) == 1

    buffer.clear()
    assert len(buffer) == 0


def test_history_buffer_query_by_level() -> None:
    buffer = EventHistoryBuffer(max_size=10)
    buffer.append(LogEvent(timestamp=0.0, level="INFO", message="Info 1"))
    buffer.append(LogEvent(timestamp=1.0, level="ERROR", message="Error 1"))
    buffer.append(LogEvent(timestamp=2.0, level="INFO", message="Info 2"))

    results = buffer.query(level="ERROR")
    assert len(results) == 1
    assert results[0].message == "Error 1"


def test_history_buffer_query_by_component() -> None:
    buffer = EventHistoryBuffer(max_size=10)
    buffer.append(LogEvent(timestamp=0.0, level="INFO", message="M1", component="Server"))
    buffer.append(LogEvent(timestamp=1.0, level="INFO", message="M2", component="Router"))
    buffer.append(LogEvent(timestamp=2.0, level="INFO", message="M3", component="Server"))

    results = buffer.query(component="Server")
    assert len(results) == 2
    assert results[0].message == "M1"
    assert results[1].message == "M3"


def test_history_buffer_query_by_time_range() -> None:
    buffer = EventHistoryBuffer(max_size=10)
    for i in range(5):
        buffer.append(LogEvent(timestamp=float(i * 10), level="INFO", message=f"M{i}"))

    results = buffer.query(since=15.0, until=35.0)
    assert len(results) == 2
    assert results[0].message == "M2"  # timestamp 20
    assert results[1].message == "M3"  # timestamp 30


def test_history_buffer_query_combined_filters() -> None:
    buffer = EventHistoryBuffer(max_size=10)
    buffer.append(LogEvent(timestamp=10.0, level="INFO", message="M1", component="Server"))
    buffer.append(LogEvent(timestamp=20.0, level="ERROR", message="M2", component="Server"))
    buffer.append(LogEvent(timestamp=30.0, level="ERROR", message="M3", component="Router"))

    results = buffer.query(level="ERROR", component="Server")
    assert len(results) == 1
    assert results[0].message == "M2"


# =============================================================================
# Tests for SimLogger
# =============================================================================


def test_simlogger_global_level() -> None:
    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")
        assert SimLogger.get_level() == "DEBUG"

        SimLogger.set_level("error")  # lowercase should be uppercased
        assert SimLogger.get_level() == "ERROR"
    finally:
        SimLogger.set_level(original_level)


def test_simlogger_logs_to_history() -> None:
    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")
        env = Environment()
        env.run(until=50)

        env.info("Test info message", component="TestComponent", extra_key="value")

        history = list(env.log_history)
        assert len(history) == 1
        assert history[0].timestamp == 50.0
        assert history[0].level == "INFO"
        assert history[0].message == "Test info message"
        assert history[0].component == "TestComponent"
        assert history[0].extra == {"extra_key": "value"}
    finally:
        SimLogger.set_level(original_level)
        env.logger.close()


def test_simlogger_all_levels() -> None:
    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")
        env = Environment()

        env.debug("Debug message")
        env.info("Info message")
        env.warning("Warning message")
        env.error("Error message")

        history = list(env.log_history)
        assert len(history) == 4
        assert [e.level for e in history] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    finally:
        SimLogger.set_level(original_level)
        env.logger.close()


def test_simlogger_respects_global_level() -> None:
    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("WARNING")
        env = Environment()

        env.debug("Should not log")
        env.info("Should not log")
        env.warning("Should log")
        env.error("Should log")

        history = list(env.log_history)
        assert len(history) == 2
        assert [e.level for e in history] == ["WARNING", "ERROR"]
    finally:
        SimLogger.set_level(original_level)
        env.logger.close()


def test_simlogger_component_filtering_disable() -> None:
    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")
        env = Environment()

        env.logger.disable_component("Server")

        env.info("Should log", component="Router")
        env.info("Should not log", component="Server")
        env.info("Should log no component")

        history = list(env.log_history)
        assert len(history) == 2
        assert history[0].message == "Should log"
        assert history[1].message == "Should log no component"
    finally:
        SimLogger.set_level(original_level)
        env.logger.close()


def test_simlogger_component_filtering_enable() -> None:
    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")
        env = Environment()

        # Disable then re-enable
        env.logger.disable_component("Server")
        env.logger.enable_component("Server")

        env.info("Should log", component="Server")

        history = list(env.log_history)
        assert len(history) == 1
    finally:
        SimLogger.set_level(original_level)
        env.logger.close()


def test_simlogger_file_output_text() -> None:
    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = Path(f.name)

        env = Environment(log_file=log_path, log_format="text")
        env.run(until=100)
        env.info("Test message", component="Server")
        env.logger.close()

        content = log_path.read_text()
        assert "Test message" in content
        assert "INFO" in content
        assert "Server" in content
        # Check time format is present
        assert "0.0d 00:01:40.00" in content

        log_path.unlink()
    finally:
        SimLogger.set_level(original_level)


def test_simlogger_file_output_json() -> None:
    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = Path(f.name)

        env = Environment(log_file=log_path, log_format="json")
        env.run(until=50)
        env.info("Test message", component="Server", job_id="abc")
        env.logger.close()

        content = log_path.read_text()
        data = json.loads(content.strip())

        assert data["sim_time"] == 50.0
        assert data["level"] == "INFO"
        assert data["message"] == "Test message"
        assert data["component"] == "Server"
        assert data["extra"]["job_id"] == "abc"
        assert "wall_time" in data
        assert "sim_time_formatted" in data

        log_path.unlink()
    finally:
        SimLogger.set_level(original_level)


def test_simlogger_close_idempotent() -> None:
    env = Environment()
    env.logger.close()
    env.logger.close()  # Should not raise


def test_simlogger_history_size() -> None:
    original_level = SimLogger.get_level()
    try:
        SimLogger.set_level("DEBUG")
        env = Environment(log_history_size=5)

        for i in range(10):
            env.info(f"Message {i}")

        history = list(env.log_history)
        assert len(history) == 5
        assert history[0].message == "Message 5"
        assert history[4].message == "Message 9"
    finally:
        SimLogger.set_level(original_level)
        env.logger.close()


def _reset_loguru_to_single_stderr_sink(*, handler_id: int) -> None:
    loguru_logger.remove()
    core = getattr(loguru_logger, "_core", None)
    if core is not None:
        setattr(core, "handlers_count", handler_id)
    loguru_logger.add(sys.stderr)


def test_patch_loguru_default_sink_returns_when_multiple_handlers() -> None:
    import simulatte.logger as simulatte_logger

    extra_handler = loguru_logger.add(sys.stderr)
    try:
        simulatte_logger._patch_loguru_default_sink()
    finally:
        loguru_logger.remove(extra_handler)


def test_patch_loguru_default_sink_returns_when_handler0_missing() -> None:
    import simulatte.logger as simulatte_logger

    # Ensure the single handler does not have id=0.
    _reset_loguru_to_single_stderr_sink(handler_id=1)
    simulatte_logger._patch_loguru_default_sink()

    # Restore a baseline that matches typical "fresh import" expectations.
    _reset_loguru_to_single_stderr_sink(handler_id=0)
    simulatte_logger._patch_loguru_default_sink()


def test_patch_loguru_default_sink_returns_when_not_stderr() -> None:
    import simulatte.logger as simulatte_logger

    loguru_logger.remove()
    core = getattr(loguru_logger, "_core", None)
    if core is not None:
        setattr(core, "handlers_count", 0)
    loguru_logger.add(sys.stdout)

    simulatte_logger._patch_loguru_default_sink()

    # Restore baseline
    _reset_loguru_to_single_stderr_sink(handler_id=0)
    simulatte_logger._patch_loguru_default_sink()


def test_patch_loguru_default_sink_is_resilient_to_exceptions(monkeypatch) -> None:
    import simulatte.logger as simulatte_logger

    _reset_loguru_to_single_stderr_sink(handler_id=0)

    original_remove = simulatte_logger._logger.remove

    def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    monkeypatch.setattr(simulatte_logger._logger, "remove", boom)

    # Should not raise despite internal failure.
    simulatte_logger._patch_loguru_default_sink()

    # Restore baseline
    monkeypatch.setattr(simulatte_logger._logger, "remove", original_remove)
    _reset_loguru_to_single_stderr_sink(handler_id=0)
    simulatte_logger._patch_loguru_default_sink()


def test_simlogger_close_handles_missing_handler() -> None:
    env = Environment()
    handler_id = env.logger._handler_id
    assert handler_id is not None
    loguru_logger.remove(handler_id)
    env.logger.close()  # Should not raise


def test_finalize_handler_is_resilient_to_missing_handler() -> None:
    from simulatte.logger import _finalize_handler

    _finalize_handler(999_999_999)  # Should not raise
