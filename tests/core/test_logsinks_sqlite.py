"""SQLite log sink: schema, rows, ``query`` and ``execute_sql``, shared databases."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.events import LogEvent
from simulatte.job import ProductionJob
from simulatte.logsinks import SQLiteSink
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def _messages(events: list[LogEvent]) -> list[str]:
    return [e.message for e in events]


def test_create_database_and_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    env = Environment(log_db_path=db_path)
    assert db_path.exists()
    assert env.log_db.path == db_path

    conn = sqlite3.connect(str(db_path))
    columns = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
    conn.close()
    assert columns == ["env_id", "seq", "t", "kind", "type", "level", "component", "message", "data_json"]
    env.close()


def test_sqlite_query_and_execute_sql(tmp_path: Path) -> None:
    env = Environment(log_db_path=tmp_path / "test.db")
    env.run(until=10)
    env.info("Info 1", component="Server", job_id="abc123")
    env.run(until=20)
    env.error("Error 1", component="Server")
    env.run(until=30)
    env.info("Info 2", component="Router")
    env.debug("below the level")

    db = env.log_db
    (first, *_rest) = db.query()
    assert isinstance(first, LogEvent)
    assert (first.t, first.level, first.message, first.component) == (10.0, "INFO", "Info 1", "Server")
    assert first.extra == {"job_id": "abc123"}
    assert first.seq >= 0

    assert _messages(db.query()) == ["Info 1", "Error 1", "Info 2"]
    assert _messages(db.query(level="ERROR")) == ["Error 1"]
    assert _messages(db.query(level="info")) == ["Info 1", "Info 2"]  # case-insensitive
    assert _messages(db.query(component="Server")) == ["Info 1", "Error 1"]
    assert [e.t for e in db.query(since=15.0, until=25.0)] == [20.0]

    rows = db.execute_sql("SELECT COUNT(*) AS cnt FROM events WHERE env_id = ?", (db.env_id,))
    assert rows[0]["cnt"] == 3
    (row,) = db.execute_sql("SELECT * FROM events WHERE level = 'ERROR'")
    assert (row["kind"], row["type"], row["component"], row["data_json"]) == ("log", "log", "Server", "{}")
    assert list(env.log_history) == db.query()  # the history and the database hold the same records
    env.close()


def test_query_with_limit_and_offset(tmp_path: Path) -> None:
    env = Environment(log_db_path=tmp_path / "test.db")
    for i in range(10):
        env.run(until=i + 1)
        env.info(f"M{i}")

    db = env.log_db
    assert _messages(db.query(limit=3)) == ["M0", "M1", "M2"]
    assert _messages(db.query(limit=3, offset=5)) == ["M5", "M6", "M7"]
    assert _messages(db.query(offset=8)) == ["M8", "M9"]
    env.close()


def test_extra_json_serialization(tmp_path: Path) -> None:
    env = Environment(log_db_path=tmp_path / "test.db")
    extra: dict[str, Any] = {"job_id": "abc123", "count": 42, "nested": {"key": "value"}, "list": [1, 2, 3]}
    env.info("Test", **extra)
    env.info("Empty")

    full, empty = env.log_db.query()
    assert dict(full.extra) == extra
    assert empty.extra == {}
    env.close()


def test_debug_stores_domain_events(tmp_path: Path) -> None:
    env = Environment(log_level="DEBUG", log_db_path=tmp_path / "test.db")
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=100.0)
    sf.add(job)
    env.run(until=10)
    env.info("done")

    db = env.log_db
    rows = db.execute_sql("SELECT type, level, component, data_json FROM events WHERE kind = 'domain' ORDER BY seq")
    types = [row["type"] for row in rows]
    assert "job.queued" in types and "job.finished" in types
    finished = rows[types.index("job.finished")]
    assert (finished["level"], finished["component"]) == ("DEBUG", "job")
    assert '"makespan": 5.0' in finished["data_json"]
    assert _messages(db.query()) == ["done"]  # query() returns log records only
    env.close()


def test_sink_without_domain_rendering(tmp_path: Path) -> None:
    env = Environment()
    sink = SQLiteSink(tmp_path / "test.db", level="DEBUG", render_domain=False, exclude=("Noisy",))
    sink.attach(env)
    env.debug("kept")
    env.debug("dropped", component="Noisy")
    assert _messages(sink.query()) == ["kept"]
    assert sink.execute_sql("SELECT COUNT(*) AS cnt FROM events WHERE kind = 'domain'")[0]["cnt"] == 0
    env.close()


def test_log_db_requires_path() -> None:
    env = Environment()
    with pytest.raises(RuntimeError, match="SQLite storage not enabled"):
        env.log_db.query()
    assert not any(isinstance(sink, SQLiteSink) for sink in env.sinks)
    env.close()


def test_env_id(tmp_path: Path) -> None:
    env = Environment(log_db_path=tmp_path / "test.db")
    env_id = env.log_db.env_id
    assert isinstance(env_id, str)
    assert len(env_id) == 32  # UUID hex string length
    env.close()


def test_closed_or_unattached_sink_raises(tmp_path: Path) -> None:
    unattached = SQLiteSink(tmp_path / "test.db")
    with pytest.raises(RuntimeError, match="not attached or already closed"):
        unattached.execute_sql("SELECT 1")
    unattached.close()  # nothing to release
    assert not (tmp_path / "test.db").exists()  # the database opens when the sink attaches

    with Environment(log_db_path=tmp_path / "test.db") as env:
        env.info("Test")
        db = env.log_db
        assert len(db.query()) == 1
    assert db.closed
    with pytest.raises(RuntimeError, match="not attached or already closed"):
        db.query()
    db.close()  # idempotent


def test_multiple_envs_shared_db(tmp_path: Path) -> None:
    db_path = tmp_path / "shared.db"
    env1 = Environment(log_db_path=db_path)
    env1_id = env1.log_db.env_id
    env1.info("Env1 message")

    env2 = Environment(log_db_path=db_path)
    env2_id = env2.log_db.env_id
    env2.info("Env2 message")
    env1.info("Env1 message 2")

    assert _messages(env1.log_db.query()) == ["Env1 message", "Env1 message 2"]  # isolated by env_id
    assert _messages(env2.log_db.query()) == ["Env2 message"]
    env1.close()

    rows = env2.log_db.execute_sql("SELECT env_id, message FROM events ORDER BY rowid")
    assert [(row["env_id"], row["message"]) for row in rows] == [
        (env1_id, "Env1 message"),
        (env2_id, "Env2 message"),
        (env1_id, "Env1 message 2"),
    ]
    env2.close()


def test_sequential_runs_accumulate_data(tmp_path: Path) -> None:
    db_path = tmp_path / "persist.db"
    with Environment(log_db_path=db_path) as env:
        env.info("Run 1 - Message 1")
        env.info("Run 1 - Message 2")

    with Environment(log_db_path=db_path) as env:
        env.info("Run 2 - Message 1")
        assert env.log_db.execute_sql("SELECT COUNT(*) AS cnt FROM events")[0]["cnt"] == 3


def test_concurrent_environments_share_db(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    Environment(log_db_path=db_path).close()  # create the schema once

    def run(thread_id: int) -> None:
        with Environment(log_db_path=db_path) as env:
            for i in range(10):
                env.info(f"Thread{thread_id}-M{i}")

    threads = [threading.Thread(target=run, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conn = sqlite3.connect(str(db_path))
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 50  # 5 threads * 10 events
    conn.close()
