"""Shared data-only experiment queries for the writer and recovery reader."""

from typing import Any

from simulatte.execution.store import _read


def experiment(store, experiment_id: str) -> dict:
    with store._transaction() as db:
        row = db.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
    if row is None:
        raise KeyError(experiment_id)
    return {**dict(row), "definition": _read(row["definition"])}


def experiment_run(store, run_id: str) -> dict:
    with store._transaction() as db:
        row = db.execute("SELECT * FROM experiment_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        configurations = [
            {
                "index": item["idx"],
                "label": item["label"],
                "parameters": _read(item["parameters"]),
                "layout_hash": item["layout_hash"],
                "layout_ref": _read(item["layout_ref"]),
            }
            for item in db.execute("SELECT * FROM configurations WHERE run=? ORDER BY idx", (run_id,))
        ]
    return {
        **dict(row),
        "preparation": _read(row["preparation"]),
        "details": _read(row["details"]),
        "configurations": configurations,
    }


def replications(store, run_id: str, *, status: str | None = None) -> list[dict]:
    """Replications in dispatch order: replication-major, then configuration."""
    with store._transaction() as db:
        rows = db.execute(
            "SELECT * FROM replications WHERE run=? AND (? IS NULL OR status=?) ORDER BY idx,configuration",
            (run_id, status, status),
        ).fetchall()
    return [
        {
            "configuration": row["configuration"],
            "index": row["idx"],
            "seed": int(row["seed"]),
            "worker_kind": row["worker_kind"],
            "request_id": row["request_id"],
            "status": row["status"],
            "attempt": row["attempt"],
            "dispatches": row["dispatches"],
        }
        for row in rows
    ]


def replication_kpis(store, run_id: str) -> dict[tuple[int, int], dict[str, Any]]:
    """KPI scalars of the succeeded and reused replications, by ``(configuration, index)``."""
    with store._transaction() as db:
        rows = db.execute(
            "SELECT r.configuration,r.idx,k.name,k.value FROM replications r "
            "JOIN kpis k ON k.attempt=r.attempt WHERE r.run=? AND r.status IN ('succeeded','reused')",
            (run_id,),
        ).fetchall()
    scalars: dict[tuple[int, int], dict[str, Any]] = {}
    for configuration, index, name, value in rows:
        scalars.setdefault((configuration, index), {})[name] = _read(value)
    return scalars
