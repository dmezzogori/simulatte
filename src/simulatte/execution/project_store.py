"""Project associations and history layered on the single execution-store writer."""

from dataclasses import dataclass

from simulatte.execution.schema import AttemptKey
from simulatte.execution.store import RunStore, _blob, _read
from simulatte.projects.config import _validate


@dataclass(frozen=True, slots=True)
class HistoryPage:
    items: tuple[dict, ...]
    next_cursor: str | None


def history(db, version, model_id, *, after=None, limit=50):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("History limit must be between 1 and 200")
    high = db.execute("SELECT COALESCE(MAX(generation),0) FROM attempts").fetchone()[0]
    last = high + 1
    if after is not None:
        try:
            high, last = map(int, after.split(":"))
            if high < 0 or last < 1 or last > high + 1:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("Invalid history cursor") from None
    if version >= 3:
        fields = "m.model AS model_id,n.name,n.text,n.important"
        joins = "LEFT JOIN attempt_models m ON m.attempt=a.id LEFT JOIN history_annotations n ON n.attempt=a.id"
        condition = "" if model_id is None else " AND m.model=?"
    else:
        fields = "NULL AS model_id,NULL AS name,NULL AS text,NULL AS important"
        joins = ""
        condition = "" if model_id is None else " AND 0"
    arguments = [high, last]
    if model_id is not None and version >= 3:
        arguments.append(model_id)
    arguments.append(limit + 1)
    rows = db.execute(
        f"""SELECT a.*, {fields}, r.attempt IS NOT NULL AS has_result
        FROM attempts a JOIN requests q ON q.id=a.request_id
        LEFT JOIN results r ON r.attempt=a.id {joins}
        WHERE a.generation<=? AND a.generation<? AND q.content_id IS NOT NULL {condition}
        ORDER BY a.generation DESC LIMIT ?""",
        arguments,
    ).fetchall()
    items = []
    for row in rows[:limit]:
        item = dict(row)
        item["details"] = _read(item["details"])
        item["important"] = bool(item["important"])
        item["name"] = item["name"] or ""
        item["text"] = item["text"] or ""
        items.append(item)
    cursor = f"{high}:{rows[limit - 1]['generation']}" if len(rows) > limit else None
    return HistoryPage(tuple(items), cursor)


class ProjectStore:
    def __init__(self, store: RunStore):
        self.store = store

    def register(self, workspace):
        _validate(workspace)
        with self.store._transaction() as db:
            old = db.execute("SELECT id FROM project_meta WHERE singleton=1").fetchone()
            if old is not None and old[0] != workspace.id:
                raise ValueError("Project UUID differs from saved history; open the original project declaration")
            db.execute("INSERT OR IGNORE INTO project_meta VALUES (1,?,?)", (workspace.id, _blob({})))
            db.execute("UPDATE models SET tombstone=1")
            for model in workspace.models:
                db.execute(
                    """INSERT INTO models VALUES (?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET
                    key=excluded.key,name=excluded.name,entrypoint=excluded.entrypoint,
                    layout=excluded.layout,tombstone=0""",
                    (model.id, model.key, model.name, model.entrypoint, model.layout),
                )

    def associate_attempt(self, key: AttemptKey, model_id: str):
        with self.store._transaction() as db:
            row = db.execute("SELECT request_id,generation FROM attempts WHERE id=?", (key.attempt_id,)).fetchone()
            if row is None or tuple(row) != (key.request_id, key.generation):
                raise KeyError(key.attempt_id)
            old = db.execute("SELECT model FROM attempt_models WHERE attempt=?", (key.attempt_id,)).fetchone()
            if old is not None and old[0] != model_id:
                raise ValueError("Attempt already associated with a different model")
            if db.execute("SELECT id FROM models WHERE id=?", (model_id,)).fetchone() is None:
                raise KeyError(model_id)
            db.execute("INSERT OR IGNORE INTO attempt_models VALUES (?,?)", (key.attempt_id, model_id))

    def history(self, model_id=None, *, after=None, limit=50):
        with self.store._mutex:
            return history(self.store._db, 3, model_id, after=after, limit=limit)

    def annotate(self, attempt_id, *, name, text, important):
        if (
            not isinstance(name, str)
            or len(name) > 200
            or not isinstance(text, str)
            or len(text) > 10000
            or type(important) is not bool
        ):
            raise ValueError("Invalid history annotation")
        with self.store._transaction() as db:
            db.execute(
                """INSERT INTO history_annotations VALUES (?,?,?,?) ON CONFLICT(attempt) DO UPDATE SET
                name=excluded.name,text=excluded.text,important=excluded.important""",
                (attempt_id, name, text, important),
            )

    def link(self, owner_kind, owner_id, target_kind, target_id):
        with self.store._transaction() as db:
            for kind, identity in ((owner_kind, owner_id), (target_kind, target_id)):
                require_reference(db, kind, identity)
            db.execute(
                'INSERT OR IGNORE INTO "references" VALUES (?,?,?,?)', (owner_kind, owner_id, target_kind, target_id)
            )


def require_reference(db, kind, identity):
    tables = {
        "analysis": ("analysis_executions", "id"),
        "analysis-snapshot": ("analysis_input_snapshots", "id"),
        "analysis-definition": ("analysis_definitions", "id"),
        "model": ("models", "id"),
        "attempt": ("attempts", "id"),
        "result": ("results", "attempt"),
        "request": ("requests", "id"),
        "snapshot": ("snapshots", "id"),
        "environment": ("environment_generations", "id"),
        "experiment": ("experiment_runs", "id"),
    }
    if kind not in tables or not isinstance(identity, str):
        raise ValueError("Unsupported reference kind")
    table, column = tables[kind]
    if db.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (identity,)).fetchone() is None:
        raise KeyError(identity)


def protected_results(db):
    """Follow saved reference chains, protecting every result/attempt/request in their closure."""
    rows = db.execute("""WITH RECURSIVE targets(kind,id) AS (
        SELECT target_kind,target_id FROM "references"
        UNION SELECT r.target_kind,r.target_id FROM "references" r JOIN targets t
            ON r.owner_kind=t.kind AND r.owner_id=t.id)
        SELECT a.id FROM attempts a JOIN targets t
        ON (t.kind IN ('attempt','result') AND t.id=a.id) OR (t.kind='request' AND t.id=a.request_id)
        UNION SELECT attempt FROM history_annotations WHERE important=1""").fetchall()
    return {row[0] for row in rows}


def latest_model_revision(db, model_id):
    from simulatte.execution.schema import Revision

    row = db.execute(
        """SELECT r.number,r.digest FROM revisions r JOIN model_revisions m ON m.revision=r.number
        WHERE m.model=? ORDER BY r.number DESC LIMIT 1""",
        (model_id,),
    ).fetchone()
    return None if row is None else Revision(*row)


def revision_is_current(store, db, revision):
    row = db.execute("SELECT model FROM model_revisions WHERE revision=?", (revision.number,)).fetchone()
    current = store._current_revision(db) if row is None else latest_model_revision(db, row[0])
    return current == revision


class ModelStore:
    """Revision-scoped view of the same writer, never a second store connection."""

    def __init__(self, store, model_id):
        self.store, self.model_id = store, model_id

    def __getattr__(self, name):
        return getattr(self.store, name)

    def current_revision(self):
        return self.store.current_revision(model_id=self.model_id)

    def create_revision(self, inputs, **kwargs):
        kwargs["revoke_active"] = False
        return self.store.create_revision(inputs, model_id=self.model_id, **kwargs)

    def next_revision_number(self):
        current = self.store.current_revision()
        return 1 if current is None else current.number + 1
