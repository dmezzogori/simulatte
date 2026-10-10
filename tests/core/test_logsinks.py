"""Logging on the event bus: ``log`` events, the text, JSON and history sinks, levels and component filters."""

from __future__ import annotations

import dataclasses
import io
import json
import sqlite3
from pathlib import Path
from typing import TextIO

import pytest

from simulatte.environment import Environment
from simulatte.events import DomainEvent, LogEvent
from simulatte.intralogistics import events as _intralogistics_events  # noqa: F401  # registers the event types
from simulatte.job import ProductionJob
from simulatte.logsinks import HistorySink, JsonSink, LogSink, TextSink, _format_sim_time
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def _domain_event_types() -> list[type[DomainEvent]]:
    found: list[type[DomainEvent]] = []
    pending: list[type[DomainEvent]] = [DomainEvent]
    while pending:
        cls = pending.pop()
        for sub in cls.__subclasses__():
            pending.append(sub)
            if "type_name" in sub.__dict__:
                found.append(sub)
    return found


def _one_job(env: Environment) -> None:
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=100.0)
    sf.add(job)
    env.run(until=10)


# =============================================================================
# _format_sim_time
# =============================================================================


def test_format_sim_time_zero() -> None:
    # When input is int, // returns int, so days=0 formats as "00"
    assert _format_sim_time(0) == "00d 00:00:0.00"


def test_format_sim_time_seconds_only() -> None:
    # When input is float, // returns float, so days=0.0 formats as "0.0"
    assert _format_sim_time(45.5) == "0.0d 00:00:45.50"


def test_format_sim_time_minutes() -> None:
    assert _format_sim_time(125.25) == "0.0d 00:02:5.25"  # 2 minutes, 5.25 seconds


def test_format_sim_time_hours() -> None:
    assert _format_sim_time(3661.5) == "0.0d 01:01:1.50"  # 1 hour, 1 minute, 1.5 seconds


def test_format_sim_time_days() -> None:
    assert _format_sim_time(90061.75) == "1.0d 01:01:1.75"  # 1 day, 1 hour, 1 minute, 1.75 seconds


def test_format_sim_time_multiple_days() -> None:
    assert _format_sim_time(259200.0) == "3.0d 00:00:0.00"


# =============================================================================
# log events
# =============================================================================


def test_log_methods_emit_log_events() -> None:
    env = Environment(log_level="DEBUG")
    seen: list[LogEvent] = []
    env.bus.subscribe(seen.append, (LogEvent,))
    env.run(until=50)

    env.debug("d")
    env.info("i", component="Main", key="value")
    env.warning("w")
    env.error("e")

    assert [(e.level, e.message) for e in seen] == [("DEBUG", "d"), ("INFO", "i"), ("WARNING", "w"), ("ERROR", "e")]
    assert seen[1].t == 50 and seen[1].component == "Main" and seen[1].extra == {"key": "value"}
    assert [e.seq for e in seen] == sorted(e.seq for e in seen)
    assert not seen[1].deltas.ops
    env.close()


def test_log_event_defaults_and_immutable() -> None:
    event = LogEvent(level="DEBUG", message="Test")
    assert event.component is None
    assert event.extra == {}
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.message = "other"  # ty: ignore[invalid-assignment]


def test_log_event_not_built_without_log_subscribers() -> None:
    env = Environment()
    env.close()  # closing cancels the default sinks' subscriptions
    assert not env.wants(LogEvent)
    seq = env._seq
    env.info("nobody listens")
    assert env._seq == seq


def test_debug_mode_validates_extra() -> None:
    env = Environment(debug=True)
    env.info("fine", count=1, ratio=0.5, name="x")
    with pytest.raises(TypeError):
        env.info("not a wire value", job=object())
    env.close()


def test_debug_mode_freezes_extra() -> None:
    """Event contents are immutable in debug mode (R30): a list in ``extra`` is recorded as a tuple, a copy the
    caller can no longer change."""
    env = Environment(debug=True)
    values = [1, 2]
    env.info("frozen", values=values, table={"k": values})
    values.append(3)
    (record,) = [event for event in env.log_history if event.message == "frozen"]
    assert record.extra == {"values": (1, 2), "table": {"k": (1, 2)}}
    env.close()


# =============================================================================
# text and JSON sinks
# =============================================================================


def test_info_reaches_text_sink() -> None:
    stream = io.StringIO()
    env = Environment()
    sink = TextSink(stream).attach(env)
    env.run(until=100)

    env.info("Test message", component="Server")
    env.debug("hidden at INFO")

    assert stream.getvalue() == "0.0d 00:01:40.00 | INFO     | Server       | Test message\n"
    assert sink in env.sinks and sink.level == "INFO" and sink.env_id is not None
    env.close()
    assert sink.closed and not stream.closed  # a stream the sink did not open stays open


def test_default_text_sink_writes_to_current_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    env = Environment()
    env.warning("to stderr", component="Main")
    assert "WARNING  | Main         | to stderr" in capsys.readouterr().err
    env.close()


def test_text_sink_file_output(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    env = Environment(log_file=log_path, log_format="text")
    assert log_path.exists()  # opened once, when attached
    env.run(until=100)
    env.info("Test message", component="Server")
    env.info("No component")
    env.close()

    lines = log_path.read_text().splitlines()
    assert lines == [
        "0.0d 00:01:40.00 | INFO     | Server       | Test message",
        "0.0d 00:01:40.00 | INFO     | -            | No component",
    ]

    with Environment(log_file=str(log_path)) as env:  # appends
        env.info("Again")
    assert log_path.read_text().splitlines()[-1].endswith("| Again")


def test_json_sink_file_output(tmp_path: Path) -> None:
    log_path = tmp_path / "run.jsonl"
    env = Environment(log_file=log_path, log_format="json")
    env.run(until=50)
    env.info("Test message", component="Server", job_id="abc", nested={"a": (1, 2)})
    env.close()

    data = json.loads(log_path.read_text().strip())
    assert data["sim_time"] == 50
    assert data["sim_time_formatted"] == "0.0d 00:00:50.00"
    assert data["level"] == "INFO"
    assert data["message"] == "Test message"
    assert data["component"] == "Server"
    assert data["extra"] == {"job_id": "abc", "nested": {"a": [1, 2]}}
    assert data["kind"] == "log" and data["type"] == "log" and isinstance(data["seq"], int)
    assert "wall_time" in data


def test_json_sink_to_stream_falls_back_to_str() -> None:
    stream = io.StringIO()
    env = Environment()
    JsonSink(stream).attach(env)
    env.info("odd extra", value=Path("x"))
    assert json.loads(stream.getvalue())["extra"] == {"value": "x"}
    env.close()


def test_debug_renders_domain_events() -> None:
    text, lines = io.StringIO(), io.StringIO()
    env = Environment(log_level="DEBUG")
    TextSink(text, level="DEBUG").attach(env)
    JsonSink(lines, level="DEBUG", exclude=("operation",)).attach(env)
    _one_job(env)

    rendered = text.getvalue().splitlines()
    queued = next(line for line in rendered if "job.queued" in line)
    assert queued.startswith("0.0d 00:00:0.00 | DEBUG    | job          | job.queued job='job-0' server=")
    assert "queue_length=1" in queued
    assert any("| operation    | operation.completed" in line for line in rendered)

    records = [json.loads(line) for line in lines.getvalue().splitlines()]
    assert all(record["kind"] == "domain" and record["level"] == "DEBUG" for record in records)
    finished = next(record for record in records if record["type"] == "job.finished")
    assert finished["component"] == "job"
    assert finished["data"]["job"] == "job-0" and finished["data"]["makespan"] == 5.0
    assert not any(record["component"] == "operation" for record in records)  # excluded by the filter
    env.close()


def test_render_domain_off_or_above_debug_subscribes_to_log_events_only() -> None:
    env = Environment()
    TextSink(io.StringIO(), level="DEBUG", render_domain=False).attach(env)
    JsonSink(io.StringIO(), level="INFO").attach(env)
    assert not any(env.wants(cls) for cls in _domain_event_types())
    env.close()


def test_default_sinks_want_no_domain_events(tmp_path: Path) -> None:
    classes = _domain_event_types()
    assert len(classes) > 30
    for env in (
        Environment(),
        Environment(log_level="WARNING", log_file=tmp_path / "a.log", log_db_path=tmp_path / "a.db"),
        Environment(log_format="json", log_file=tmp_path / "b.log"),
    ):
        assert env.wants(LogEvent)
        assert not any(env.wants(cls) for cls in classes)
        env.close()

    env = Environment(log_level="DEBUG")  # only a sink at DEBUG with render_domain subscribes to "*"
    assert all(env.wants(cls) for cls in classes)
    env.close()


# =============================================================================
# levels and component filters
# =============================================================================


def test_independent_levels_per_env() -> None:
    quiet = Environment(log_level="warning")
    verbose = Environment(log_level="DEBUG")
    for env in (quiet, verbose):
        env.debug("d")
        env.info("i")
        env.warning("w")
        env.error("e")

    assert [e.level for e in quiet.log_history] == ["WARNING", "ERROR"]
    assert [e.level for e in verbose.log_history] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert quiet.log_history.level == "WARNING"
    quiet.close()
    verbose.close()


def test_unknown_level_or_format_rejected() -> None:
    with pytest.raises(ValueError, match="unknown log level 'LOUD'"):
        Environment(log_level="LOUD")
    with pytest.raises(ValueError, match="unknown log level"):
        HistorySink(level=10)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="log_format"):
        Environment(log_format="xml")  # ty: ignore[invalid-argument-type]


def test_component_filters() -> None:
    env = Environment()
    history = env.log_history
    history.disable_component("Server")
    only = HistorySink(components=("Router",)).attach(env)
    assert isinstance(only, HistorySink)
    without = HistorySink(exclude=("Router",)).attach(env)

    env.info("router", component="Router")
    env.info("server", component="Server")
    env.info("no component")

    assert [e.message for e in history] == ["router", "no component"]
    assert [e.message for e in only] == ["router", "no component"]  # records without a component always pass
    assert [e.message for e in without] == ["server", "no component"]

    history.enable_component("Server")
    only.enable_component("Server")
    without.disable_component("Server")
    without.enable_component("Router")
    env.info("server again", component="Server")
    env.info("router again", component="Router")

    assert [e.message for e in history][-2:] == ["server again", "router again"]
    assert [e.message for e in only][-2:] == ["server again", "router again"]
    assert [e.message for e in without][-1] == "router again"
    env.close()


# =============================================================================
# history
# =============================================================================


def test_history_query() -> None:
    env = Environment(log_level="DEBUG")
    for i, (level, component) in enumerate(
        [("INFO", "Server"), ("ERROR", "Server"), ("ERROR", "Router"), ("DEBUG", None), ("INFO", "Server")]
    ):
        env.run(until=10 * (i + 1))
        getattr(env, level.lower())(f"M{i}", component=component)

    def messages(events: list[LogEvent]) -> list[str]:
        return [e.message for e in events]

    history = env.log_history
    assert messages(history.query(level="error")) == ["M1", "M2"]
    assert messages(history.query(component="Server")) == ["M0", "M1", "M4"]
    assert messages(history.query(since=15.0, until=35.0)) == ["M1", "M2"]
    assert messages(history.query(since=40.0)) == ["M3", "M4"]
    assert messages(history.query(level="ERROR", component="Server")) == ["M1"]
    assert messages(history.query()) == [f"M{i}" for i in range(5)]
    assert history.query()[0].t == 10
    assert history.query()[0].extra == {}

    history.clear()
    assert len(history) == 0
    env.close()


def test_history_size() -> None:
    env = Environment(log_history_size=5)
    for i in range(10):
        env.info(f"Message {i}")

    assert env.log_history.maxlen == 5
    assert [e.message for e in env.log_history] == [f"Message {i}" for i in range(5, 10)]
    env.close()


def test_history_logs_with_time_component_and_extra() -> None:
    env = Environment()
    env.run(until=50)
    env.info("Test info message", component="TestComponent", extra_key="value")

    (event,) = env.log_history
    assert event.t == 50
    assert event.level == "INFO"
    assert event.message == "Test info message"
    assert event.component == "TestComponent"
    assert event.extra == {"extra_key": "value"}
    env.close()


# =============================================================================
# attachment and closing
# =============================================================================


def test_sinks_closed_on_env_close(tmp_path: Path) -> None:
    env = Environment(log_file=tmp_path / "run.log", log_db_path=tmp_path / "run.db")
    extra = TextSink(tmp_path / "extra.log").attach(env)
    sinks = env.sinks
    assert [type(s).__name__ for s in sinks] == ["TextSink", "HistorySink", "SQLiteSink", "TextSink"]
    assert sinks[-1] is extra
    env.info("before")

    env.close()
    assert all(s.closed for s in sinks)
    assert not env.wants(LogEvent)
    env.info("after")  # no sink receives it
    assert [e.message for e in env.log_history] == ["before"]  # the history keeps its records
    assert (tmp_path / "extra.log").read_text().count("\n") == 1

    env.close()  # idempotent
    extra.close()


def test_context_manager_closes_sinks(tmp_path: Path) -> None:
    with Environment(log_file=tmp_path / "run.log") as env:
        env.info("inside")
    assert all(s.closed for s in env.sinks)


def test_attach_once() -> None:
    env = Environment()
    sink = HistorySink()
    sink.attach(env)
    with pytest.raises(RuntimeError, match="already attached"):
        sink.attach(Environment())
    sink.close()
    with pytest.raises(RuntimeError, match="is closed"):
        sink.attach(env)
    assert isinstance(sink, LogSink)
    env.close()


def test_failed_environment_setup_closes_open_sinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[TextSink] = []
    streams: list[TextIO] = []
    original = TextSink._open

    def track(sink: TextSink) -> None:
        original(sink)
        opened.append(sink)
        assert sink._file is not None
        streams.append(sink._file)

    monkeypatch.setattr(TextSink, "_open", track)
    with pytest.raises(sqlite3.OperationalError, match="unable to open database"):
        Environment(log_file=tmp_path / "run.log", log_db_path=tmp_path / "missing" / "run.db")
    assert len(opened) == 1 and opened[0].closed
    assert streams[0].closed


def test_filtered_logs_do_not_build_events_or_consume_sequence() -> None:
    with Environment(debug=True, log_level="INFO") as env:
        seq = env._seq
        env.debug("filtered", invalid=object())  # freezing this extra would raise
        assert env._seq == seq
        subscriber = env.bus.subscribe(lambda event: None, "**")
        with pytest.raises(TypeError):
            env.debug("observed", invalid=object())
        subscriber.cancel()
        env.debug("filtered again", invalid=object())
        assert env._seq == seq
        sink = HistorySink(level="DEBUG").attach(env)
        env.debug("kept")
        assert [e.message for e in sink] == ["kept"]
        sink.close()
        env.debug("filtered once more", invalid=object())
        assert env._seq == seq + 1


def test_close_stops_delivery_to_collectors_and_other_subscribers() -> None:
    env = Environment()
    seen: list[LogEvent] = []
    env.bus.subscribe(seen.append, (LogEvent,))
    env.info("before")
    env.close()
    seq = env._seq
    event = LogEvent(level="INFO", message="after")
    env.emit(event)
    env.info("also after")
    assert [e.message for e in seen] == ["before"]
    assert env._seq == seq and event.seq == -1
