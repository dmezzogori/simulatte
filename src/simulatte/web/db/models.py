"""Database schema and initialization for Simulatte Web UI."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_connection: sqlite3.Connection | None = None
_db_path: Path | None = None

SCHEMA = """
-- Simulation runs table
CREATE TABLE IF NOT EXISTS simulation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    until_time REAL,
    seed INTEGER,
    config TEXT,
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    progress REAL DEFAULT 0.0
);

-- Snapshots table for replay
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES simulation_runs(id) ON DELETE CASCADE,
    sim_time REAL NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sim_time)
);

-- Analytics cache table
CREATE TABLE IF NOT EXISTS analytics_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES simulation_runs(id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    metadata TEXT,
    UNIQUE(run_id, metric_name)
);

-- User preferences table
CREATE TABLE IF NOT EXISTS user_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_runs_status ON simulation_runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_env ON simulation_runs(env_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(run_id, sim_time);
CREATE INDEX IF NOT EXISTS idx_analytics_run ON analytics_cache(run_id);
"""


def init_db(db_path: Path) -> None:
    """Initialize the database with schema.

    Args:
        db_path: Path to the SQLite database file.
    """
    global _connection, _db_path
    _db_path = db_path

    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        _connection = sqlite3.connect(str(db_path), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA busy_timeout=5000")
        _connection.execute("PRAGMA foreign_keys=ON")
        _connection.executescript(SCHEMA)
        _connection.commit()


def get_connection() -> sqlite3.Connection:
    """Get the database connection."""
    if _connection is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _connection


@dataclass
class SimulationRun:
    """Represents a simulation run in the database."""

    id: int
    env_id: str
    started_at: str
    completed_at: str | None
    until_time: float | None
    seed: int | None
    config: dict[str, Any] | None
    status: str
    error_message: str | None
    progress: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SimulationRun:
        """Create from a database row."""
        return cls(
            id=row["id"],
            env_id=row["env_id"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            until_time=row["until_time"],
            seed=row["seed"],
            config=json.loads(row["config"]) if row["config"] else None,
            status=row["status"],
            error_message=row["error_message"],
            progress=row["progress"] or 0.0,
        )


@dataclass
class Snapshot:
    """Represents a simulation snapshot."""

    id: int
    run_id: int
    sim_time: float
    state: dict[str, Any]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Snapshot:
        """Create from a database row."""
        return cls(
            id=row["id"],
            run_id=row["run_id"],
            sim_time=row["sim_time"],
            state=json.loads(row["state"]),
            created_at=row["created_at"],
        )


def create_run(
    env_id: str,
    until_time: float | None = None,
    seed: int | None = None,
    config: dict[str, Any] | None = None,
) -> int:
    """Create a new simulation run record.

    Returns:
        The ID of the created run.
    """
    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    config_json = json.dumps(config) if config else None

    with _lock:
        cursor = conn.execute(
            """
            INSERT INTO simulation_runs (env_id, started_at, until_time, seed, config, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (env_id, now, until_time, seed, config_json),
        )
        conn.commit()
        return cursor.lastrowid or 0


def update_run_status(
    run_id: int,
    status: str,
    progress: float | None = None,
    error_message: str | None = None,
) -> None:
    """Update the status of a simulation run."""
    conn = get_connection()

    with _lock:
        if progress is not None:
            conn.execute(
                "UPDATE simulation_runs SET status = ?, progress = ? WHERE id = ?",
                (status, progress, run_id),
            )
        else:
            conn.execute(
                "UPDATE simulation_runs SET status = ? WHERE id = ?",
                (status, run_id),
            )

        if error_message is not None:
            conn.execute(
                "UPDATE simulation_runs SET error_message = ? WHERE id = ?",
                (error_message, run_id),
            )

        if status == "completed":
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "UPDATE simulation_runs SET completed_at = ?, progress = 1.0 WHERE id = ?",
                (now, run_id),
            )

        conn.commit()


def get_run(run_id: int) -> SimulationRun | None:
    """Get a simulation run by ID."""
    conn = get_connection()
    with _lock:
        cursor = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,))
        row = cursor.fetchone()
    return SimulationRun.from_row(row) if row else None


def get_latest_run() -> SimulationRun | None:
    """Get the most recent simulation run."""
    conn = get_connection()
    with _lock:
        cursor = conn.execute("SELECT * FROM simulation_runs ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
    return SimulationRun.from_row(row) if row else None


def insert_snapshot(run_id: int, sim_time: float, state: dict[str, Any]) -> int:
    """Insert a simulation snapshot.

    Returns:
        The ID of the created snapshot.
    """
    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    state_json = json.dumps(state)

    with _lock:
        cursor = conn.execute(
            """
            INSERT OR REPLACE INTO snapshots (run_id, sim_time, state, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, sim_time, state_json, now),
        )
        conn.commit()
        return cursor.lastrowid or 0


def get_snapshots(
    run_id: int,
    limit: int | None = None,
    offset: int = 0,
) -> list[Snapshot]:
    """Get snapshots for a simulation run."""
    conn = get_connection()
    sql = "SELECT * FROM snapshots WHERE run_id = ? ORDER BY sim_time"
    params: list[Any] = [run_id]

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    if offset > 0:
        if limit is None:
            sql += " LIMIT -1"
        sql += " OFFSET ?"
        params.append(offset)

    with _lock:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()

    return [Snapshot.from_row(row) for row in rows]


def get_snapshot_count(run_id: int) -> int:
    """Get the number of snapshots for a run."""
    conn = get_connection()
    with _lock:
        cursor = conn.execute("SELECT COUNT(*) FROM snapshots WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
    return row[0] if row else 0


def get_snapshot_by_time(run_id: int, sim_time: float) -> Snapshot | None:
    """Get the snapshot closest to the given simulation time."""
    conn = get_connection()
    with _lock:
        cursor = conn.execute(
            """
            SELECT * FROM snapshots
            WHERE run_id = ? AND sim_time <= ?
            ORDER BY sim_time DESC
            LIMIT 1
            """,
            (run_id, sim_time),
        )
        row = cursor.fetchone()
    return Snapshot.from_row(row) if row else None


def set_preference(key: str, value: str) -> None:
    """Set a user preference."""
    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    with _lock:
        conn.execute(
            """
            INSERT OR REPLACE INTO user_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, value, now),
        )
        conn.commit()


def get_preference(key: str, default: str | None = None) -> str | None:
    """Get a user preference."""
    conn = get_connection()
    with _lock:
        cursor = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (key,))
        row = cursor.fetchone()
    return row["value"] if row else default


def cache_analytics(run_id: int, metric_name: str, value: float, metadata: dict[str, Any] | None = None) -> None:
    """Cache an analytics metric."""
    conn = get_connection()
    metadata_json = json.dumps(metadata) if metadata else None
    with _lock:
        conn.execute(
            """
            INSERT OR REPLACE INTO analytics_cache (run_id, metric_name, metric_value, metadata)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, metric_name, value, metadata_json),
        )
        conn.commit()


def get_cached_analytics(run_id: int) -> dict[str, Any]:
    """Get all cached analytics for a run."""
    conn = get_connection()
    with _lock:
        cursor = conn.execute(
            "SELECT metric_name, metric_value, metadata FROM analytics_cache WHERE run_id = ?",
            (run_id,),
        )
        rows = cursor.fetchall()

    result: dict[str, Any] = {}
    for row in rows:
        value = row["metric_value"]
        if row["metadata"]:
            value = {"value": value, "metadata": json.loads(row["metadata"])}
        result[row["metric_name"]] = value

    return result
