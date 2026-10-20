"""Saved presentations in the project's run store (schema 5): immutable revisions and export jobs.

Each revision owns ``presentation-revision`` references (owner id ``<id>:<revision>``) to its recording's result
and request and, for each evidence selection, to the analysis execution and its input snapshot; prune and
analysis deletion respect them. An export job pins one revision: from admission until it finishes, is cancelled
or recovery marks it lost, it owns the same targets plus the revision itself. Export bytes never enter the store;
a finished job records the name, size and SHA-256 the client reports.

Reads work over the writer or a read-only ``StoreReader``; stores older than schema 5 have no presentations.
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time
from typing import Any
from uuid import uuid4

from simulatte.presentations.schema import (
    json_value,
    manifest_hash,
    output_content_hash,
    snapshot_hash,
    validate_manifest,
)

TARGETS = ("studio", "video", "html")
ACTIVE = ("waiting", "running")
OUTCOMES = ("succeeded", "failed", "cancelled")
STATIC_MEDIA = ("image/png", "image/svg+xml")
STATIC_KINDS = ("figure", "image")
MAX_OPTIONS = 64 << 10
# A static view counts toward the standalone HTML output, itself at most 150 MiB.
MAX_STATIC_BYTES = 150 << 20
MAX_MESSAGE = 4096
_HEX64 = re.compile("[0-9a-f]{64}")


class StaleRevision(ValueError):
    """A newer revision was saved since the one an edit started from."""


@dataclass(frozen=True, slots=True)
class ExportJob:
    """One admitted export of a pinned revision; ``references`` are its leases, empty once terminal."""

    id: str
    snapshot_hash: str
    state: str
    references: tuple[tuple[str, str], ...]
    presentation_id: str = ""
    revision: int = 0
    target: str = ""
    options: Mapping[str, Any] = field(default_factory=dict)
    details: Mapping[str, Any] = field(default_factory=dict)
    created: float = 0.0
    finished: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "presentation_id": self.presentation_id,
            "revision": self.revision,
            "target": self.target,
            "snapshot_hash": self.snapshot_hash,
            "state": self.state,
            "options": dict(self.options),
            "details": dict(self.details),
            "created": self.created,
            "finished": self.finished,
            "references": [{"kind": kind, "id": identity} for kind, identity in self.references],
        }


def _dumps(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _text(value: Any, what: str, high: int, low: int = 0) -> str:
    if type(value) is not str or not low <= len(value) <= high:
        raise ValueError(f"{what} must be text of {low} to {high} characters")
    return value


def _count(value: Any, what: str, high: int) -> int:
    if type(value) is not int or not 0 <= value <= high:
        raise ValueError(f"{what} must be an integer from 0 to {high}")
    return value


def _hex64(value: Any, what: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{what} must be 64 lowercase hex digits")
    return value


def _statics(statics: Any) -> list[dict]:
    """``[{evidence_id, media_type, sha256, size}]``: the client's static views, in its order."""
    if not isinstance(statics, list) or len(statics) > 12:
        raise ValueError("Export statics must list at most 12 static views")
    result = []
    for index, item in enumerate(statics):
        where = f"Static view {index}"
        if not isinstance(item, Mapping) or set(item) != {"evidence_id", "media_type", "sha256", "size"}:
            raise ValueError(f"{where} needs exactly the fields evidence_id, media_type, sha256 and size")
        _text(item["evidence_id"], f"{where} evidence_id", 64, 1)
        if item["media_type"] not in STATIC_MEDIA:
            raise ValueError(f"{where} media type must be image/png or image/svg+xml")
        _hex64(item["sha256"], f"{where} sha256")
        _count(item["size"], f"{where} size", MAX_STATIC_BYTES)
        result.append(dict(item))
    return result


def _outcome(outcome: Any) -> dict:
    """``{state: succeeded|failed|cancelled, message?, output?: {name, size, sha256, media_type}}``."""
    if not isinstance(outcome, Mapping):
        raise ValueError("Export outcome must be an object")
    if set(outcome) - {"state", "message", "output"}:
        raise ValueError("Export outcome accepts only state, message and output")
    state = outcome.get("state")
    if state not in OUTCOMES:
        raise ValueError("Export outcome state must be succeeded, failed or cancelled")
    details: dict[str, Any] = {}
    if "message" in outcome:
        details["message"] = _text(outcome["message"], "Export message", MAX_MESSAGE)
    if (state == "succeeded") != ("output" in outcome):
        raise ValueError("A succeeded export, and only a succeeded export, reports its output")
    if "output" in outcome:
        output = outcome["output"]
        if not isinstance(output, Mapping) or set(output) != {"name", "size", "sha256", "media_type"}:
            raise ValueError("Export output needs exactly name, size, sha256 and media_type")
        details["output"] = {
            "name": _text(output["name"], "Export output name", 255, 1),
            "size": _count(output["size"], "Export output size", 2**53 - 1),
            "sha256": _hex64(output["sha256"], "Export output sha256"),
            "media_type": _text(output["media_type"], "Export output media_type", 255, 1),
        }
    return {"state": state, "details": details}


class PresentationStore:
    def __init__(self, store):
        self.store = store

    def _available(self) -> bool:
        return getattr(self.store, "version", 5) >= 5

    def _writer(self) -> None:
        from simulatte.execution.store import RunStore

        if not isinstance(self.store, RunStore):
            raise ValueError("Presentations are read-only without the project writer")

    # Revisions ---------------------------------------------------------------------------------------------

    def create(self, manifest: Mapping[str, Any]) -> str:
        """Save a new presentation at revision 1; ``manifest`` has no ``id`` or ``revision``, or revision 0."""
        self._writer()
        document = validate_manifest(manifest)
        if document.get("revision", 0) != 0:
            raise ValueError("A new presentation starts at revision 0")
        self._verify_recording(document)
        identity = str(uuid4())
        with self.store._transaction() as db:
            now = time.time()
            db.execute("INSERT INTO presentations VALUES (?,?,?,0)", (identity, document["name"], now))
            self._save(db, identity, 1, document, now)
        return identity

    def revise(self, identity: str, expected_revision: int, manifest: Mapping[str, Any]) -> int:
        """Save ``manifest`` as the revision after ``expected_revision``; a newer saved revision refuses it."""
        self._writer()
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a saved revision number")
        document = validate_manifest(manifest)
        if document.get("id", identity) != identity:
            raise ValueError("Manifest belongs to another presentation")
        self._verify_recording(document)
        with self.store._transaction() as db:
            self._live(db, identity)
            current = db.execute("SELECT MAX(revision) FROM presentation_revisions WHERE id=?", (identity,)).fetchone()[
                0
            ]
            if current != expected_revision:
                raise StaleRevision("stale revision")
            self._save(db, identity, current + 1, document, time.time())
            db.execute("UPDATE presentations SET name=? WHERE id=?", (document["name"], identity))
        return current + 1

    def get(self, identity: str, revision: int | None = None) -> dict:
        """A saved manifest, the latest by default. A deleted presentation keeps explicit revisions until prune."""
        if not self._available() or revision is not None and (type(revision) is not int or revision < 1):
            raise KeyError(identity)
        with self.store._transaction() as db:
            row = db.execute("SELECT deleted FROM presentations WHERE id=?", (identity,)).fetchone()
            if row is None or revision is None and row[0]:
                raise KeyError(identity)
            if revision is None:
                revision = db.execute(
                    "SELECT MAX(revision) FROM presentation_revisions WHERE id=?", (identity,)
                ).fetchone()[0]
            found = db.execute(
                "SELECT manifest FROM presentation_revisions WHERE id=? AND revision=?", (identity, revision)
            ).fetchone()
        if found is None:
            raise KeyError(f"{identity}:{revision}")
        return json.loads(found[0])

    def revisions(self, identity: str) -> builtins.list[dict]:
        if not self._available():
            raise KeyError(identity)
        with self.store._transaction() as db:
            rows = db.execute(
                "SELECT revision,created,content_hash FROM presentation_revisions WHERE id=? ORDER BY revision",
                (identity,),
            ).fetchall()
        if not rows:
            raise KeyError(identity)
        return [dict(row) for row in rows]

    def list(self, cursor: str | None = None, limit: int = 50) -> dict:
        """Newest presentations first; the cursor is the last row position, stable as presentations arrive."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Presentation list limit must be between 1 and 100")
        if cursor is not None and (type(cursor) is not str or not cursor.isdecimal() or str(int(cursor)) != cursor):
            raise ValueError("Invalid presentation cursor")
        if not self._available():
            return {"items": [], "next_cursor": None}
        with self.store._transaction() as db:
            rows = db.execute(
                """SELECT p.rowid AS position,p.id,p.name,p.created,r.revision,
                json_extract(CAST(r.manifest AS TEXT),'$.recording.attempt_id') AS attempt,
                (SELECT COUNT(*) FROM export_jobs e WHERE e.presentation=p.id
                    AND e.state IN ('waiting','running')) AS active,
                (SELECT COUNT(*) FROM export_jobs e WHERE e.presentation=p.id) AS total
                FROM presentations p JOIN presentation_revisions r ON r.id=p.id
                AND r.revision=(SELECT MAX(revision) FROM presentation_revisions WHERE id=p.id)
                WHERE p.deleted=0"""
                + (" AND p.rowid<?" if cursor is not None else "")
                + " ORDER BY p.rowid DESC LIMIT ?",
                (*(() if cursor is None else (int(cursor),)), limit + 1),
            ).fetchall()
        items = [
            {
                "id": row["id"],
                "name": row["name"],
                "revision": row["revision"],
                "created": row["created"],
                "recording": {"attempt_id": row["attempt"]},
                "exports": {"active": row["active"], "total": row["total"]},
            }
            for row in rows[:limit]
        ]
        return {"items": items, "next_cursor": str(rows[limit - 1]["position"]) if len(rows) > limit else None}

    def delete(self, identity: str) -> None:
        """Release a presentation's references; refused while one of its exports is active or another owner
        names it. Its rows stay readable for finished exports until ``RunStore.prune``."""
        self._writer()
        with self.store._transaction() as db:
            self._live(db, identity)
            if db.execute(
                "SELECT 1 FROM export_jobs WHERE presentation=? AND state IN ('waiting','running')", (identity,)
            ).fetchone():
                raise ValueError("Presentation has active exports; cancel them before deleting")
            revisions = (identity + ":", identity + ";")  # every ``<id>:<revision>`` owner id
            if db.execute(
                """SELECT 1 FROM "references" WHERE (target_kind='presentation' AND target_id=?)
                OR (target_kind='presentation-revision' AND target_id>=? AND target_id<?)""",
                (identity, *revisions),
            ).fetchone():
                raise ValueError("Presentation is referenced; delete that owner first")
            db.execute(
                """DELETE FROM "references" WHERE owner_kind='presentation-revision'
                AND owner_id>=? AND owner_id<?""",
                revisions,
            )
            db.execute("""DELETE FROM "references" WHERE owner_kind='presentation' AND owner_id=?""", (identity,))
            db.execute("UPDATE presentations SET deleted=1 WHERE id=?", (identity,))

    @staticmethod
    def _live(db, identity) -> None:
        if db.execute("SELECT 1 FROM presentations WHERE id=? AND deleted=0", (identity,)).fetchone() is None:
            raise KeyError(identity)

    def _verify_recording(self, document: dict) -> None:
        """The recording's committed trace has the saved hash and contains the saved time range."""
        from simulatte.trace import Trace, TraceCorrupted

        recording = document["recording"]
        attempt = recording["attempt_id"]
        with self.store._transaction() as db:
            row = db.execute(
                "SELECT a.request_id FROM results r JOIN attempts a ON a.id=r.attempt WHERE r.attempt=?", (attempt,)
            ).fetchone()
        if row is None:
            raise ValueError(f"Recording {attempt} has no saved result")
        if row[0] != recording["request_id"]:
            raise ValueError(f"Recording {attempt} belongs to another request")
        path = self.store.root / "results" / attempt / "run.simtrace"
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Recording {attempt}: trace is missing")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != recording["trace_sha256"]:
            raise ValueError(f"Recording {attempt}: trace differs from the saved trace_sha256")
        try:
            cursors = Trace.open(path).cursor_range
        except (TraceCorrupted, ValueError, OSError) as error:
            raise ValueError(f"Recording {attempt}: trace is unreadable ({error})") from None
        times = [] if cursors is None else [cursor[0] for cursor in cursors if math.isfinite(cursor[0])]
        start, end = document["time"]["start"], document["time"]["end"]
        if not times or start < min(times) or end > max(times):
            span = "no finite time" if not times else f"{min(times)} to {max(times)}"
            raise ValueError(f"Time range {start} to {end} is outside the recording ({span})")

    def _save(self, db, identity: str, revision: int, document: dict, now: float) -> None:
        manifest = json_value({**document, "id": identity, "revision": revision, "created": now})
        targets = self._targets(db, manifest)
        db.execute(
            "INSERT INTO presentation_revisions VALUES (?,?,?,?,?)",
            (identity, revision, _dumps(manifest), manifest_hash(manifest), now),
        )
        self._link(db, "presentation-revision", f"{identity}:{revision}", targets)

    @staticmethod
    def _targets(db, manifest: dict) -> builtins.list[tuple[str, str]]:
        """What a revision needs: its recording and every selected output with its input snapshot."""
        from simulatte.analysis import codec

        recording = manifest["recording"]
        targets = {("result", recording["attempt_id"]), ("request", recording["request_id"])}
        for index, item in enumerate(manifest["evidence"]):
            where = f"Evidence {index} ({item['labels']['name'] or item['output_id'][:12]})"
            execution = item["execution_id"]
            row = db.execute("SELECT state,snapshot FROM analysis_executions WHERE id=?", (execution,)).fetchone()
            if row is None:
                raise ValueError(f"{where}: analysis {execution} is unavailable")
            output = db.execute("SELECT manifest FROM analysis_outputs WHERE execution=?", (execution,)).fetchone()
            if row["state"] != "succeeded" or output is None:
                raise ValueError(f"{where}: analysis {execution} has no saved output")
            descriptor = next((o for o in codec.loads(output[0])["outputs"] if o["id"] == item["output_id"]), None)
            if descriptor is None:
                raise ValueError(f"{where}: analysis {execution} has no output {item['output_id']}")
            try:
                content = output_content_hash(descriptor)
            except ValueError:
                content = None
            if content != item["content_hash"]:
                raise ValueError(f"{where}: the output content changed; select it again")
            if descriptor["kind"] != item["kind"]:
                raise ValueError(f"{where}: the output is a {descriptor['kind']}, not a {item['kind']}")
            source = item["labels"]["source"]
            if source["execution_id"] != execution:
                raise ValueError(f"{where}: its labels name another analysis execution")
            if source["snapshot_hash"] != row["snapshot"]:
                raise ValueError(f"{where}: its labels name another input snapshot")
            targets |= {("analysis", execution), ("analysis-snapshot", row["snapshot"])}
        return sorted(targets)

    @staticmethod
    def _link(db, owner_kind: str, owner_id: str, targets) -> None:
        from simulatte.execution.project_store import require_reference

        for kind, target in targets:
            require_reference(db, kind, target)
            db.execute('INSERT OR IGNORE INTO "references" VALUES (?,?,?,?)', (owner_kind, owner_id, kind, target))

    # Export jobs -------------------------------------------------------------------------------------------

    def admit_export(
        self,
        identity: str,
        revision: int,
        target: str,
        options: Mapping[str, Any],
        statics: builtins.list | None = None,
    ) -> ExportJob:
        """Pin ``revision`` for an export (state ``waiting``) with the snapshot hash of what it will render."""
        self._writer()
        if target not in TARGETS:
            raise ValueError("Export target must be studio, video or html")
        if not isinstance(options, Mapping):
            raise ValueError("Export options must be an object")
        try:
            options = json_value(options)
        except ValueError as error:
            raise ValueError(f"Export options must be JSON data: {error}") from None
        if len(_dumps(options)) > MAX_OPTIONS:
            raise ValueError("Export options exceed 64 KiB")
        chosen = json_value(_statics([] if statics is None else statics))
        with self.store._transaction() as db:
            self._live(db, identity)
            found = db.execute(
                "SELECT manifest FROM presentation_revisions WHERE id=? AND revision=?", (identity, revision)
            ).fetchone()
            if found is None or type(revision) is not int:
                raise KeyError(f"{identity}:{revision}")
            manifest = json.loads(found[0])
            self._cover(manifest, target, chosen)
            owner = f"{identity}:{revision}"
            targets = db.execute(
                """SELECT target_kind,target_id FROM "references"
                WHERE owner_kind='presentation-revision' AND owner_id=?""",
                (owner,),
            ).fetchall()
            job = str(uuid4())
            db.execute(
                "INSERT INTO export_jobs VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                (
                    job,
                    identity,
                    revision,
                    target,
                    _dumps(options),
                    snapshot_hash(manifest, target, options, chosen),
                    "waiting",
                    _dumps({"statics": chosen}),
                    time.time(),
                ),
            )
            self._link(db, "export-job", job, [*map(tuple, targets), ("presentation-revision", owner)])
        return self.export(job)

    @staticmethod
    def _cover(manifest: dict, target: str, statics: builtins.list[dict]) -> None:
        """Statics name selected figure/image evidence once; video and HTML need one for each of them."""
        kinds = {item["id"]: item["kind"] for item in manifest["evidence"]}
        seen = set()
        for item in statics:
            evidence = item["evidence_id"]
            if evidence not in kinds:
                raise ValueError(f"Static view for {evidence}: that evidence is not selected in this revision")
            if evidence in seen:
                raise ValueError(f"Static view for {evidence} is given more than once")
            seen.add(evidence)
            if kinds[evidence] not in STATIC_KINDS:
                raise ValueError(f"Static view for {evidence}: a {kinds[evidence]} output has no static view")
            if kinds[evidence] == "figure" and item["media_type"] != "image/png":
                raise ValueError(f"Static view for {evidence}: a figure's static view is image/png")
        if target != "studio":
            for evidence, kind in kinds.items():
                if kind in STATIC_KINDS and evidence not in seen:
                    raise ValueError(f"Evidence {evidence} needs a static view for the {target} export")

    def _job(self, db, job: str):
        row = db.execute("SELECT state FROM export_jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            raise KeyError(job)
        return row["state"]

    def start_export(self, job: str) -> ExportJob:
        self._writer()
        with self.store._transaction() as db:
            state = self._job(db, job)
            if state != "waiting":
                raise ValueError(f"Export job is {state}")
            db.execute("UPDATE export_jobs SET state='running' WHERE id=?", (job,))
        return self.export(job)

    def finish_export(self, job: str, outcome: Mapping[str, Any]) -> ExportJob:
        """Record the terminal outcome and release the job's leases; a lost job never succeeds afterwards."""
        self._writer()
        checked = _outcome(outcome)
        with self.store._transaction() as db:
            state = self._job(db, job)
            if state not in ACTIVE:
                raise ValueError(f"Export job is {state}")
            if checked["state"] == "succeeded" and state != "running":
                raise ValueError("Start the export before finishing it")
            details = json.loads(db.execute("SELECT details FROM export_jobs WHERE id=?", (job,)).fetchone()[0])
            self._terminal(db, job, checked["state"], {**details, **checked["details"]})
        return self.export(job)

    def cancel_export(self, job: str) -> ExportJob:
        """Cancel an active job and release its leases; a finished job is returned unchanged."""
        self._writer()
        with self.store._transaction() as db:
            if self._job(db, job) in ACTIVE:
                details = db.execute("SELECT details FROM export_jobs WHERE id=?", (job,)).fetchone()[0]
                self._terminal(db, job, "cancelled", json.loads(details))
        return self.export(job)

    @staticmethod
    def _terminal(db, job: str, state: str, details: dict) -> None:
        db.execute(
            "UPDATE export_jobs SET state=?,details=?,finished=? WHERE id=?", (state, _dumps(details), time.time(), job)
        )
        db.execute("""DELETE FROM "references" WHERE owner_kind='export-job' AND owner_id=?""", (job,))

    @staticmethod
    def _export(db, row) -> ExportJob:
        references = db.execute(
            """SELECT target_kind,target_id FROM "references" WHERE owner_kind='export-job' AND owner_id=?
            ORDER BY target_kind,target_id""",
            (row["id"],),
        ).fetchall()
        return ExportJob(
            id=row["id"],
            snapshot_hash=row["snapshot_hash"],
            state=row["state"],
            references=tuple(tuple(reference) for reference in references),
            presentation_id=row["presentation"],
            revision=row["revision"],
            target=row["target"],
            options=json.loads(row["options"]),
            details=json.loads(row["details"]),
            created=row["created"],
            finished=row["finished"],
        )

    def export(self, job: str) -> ExportJob:
        if not self._available():
            raise KeyError(job)
        with self.store._transaction() as db:
            row = db.execute("SELECT * FROM export_jobs WHERE id=?", (job,)).fetchone()
            if row is None:
                raise KeyError(job)
            return self._export(db, row)

    def exports(self, presentation_id: str | None = None, limit: int = 100) -> builtins.list[ExportJob]:
        """The newest export jobs first, of one presentation or all."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Export list limit must be between 1 and 100")
        if not self._available():
            return []
        with self.store._transaction() as db:
            rows = db.execute(
                "SELECT * FROM export_jobs"
                + ("" if presentation_id is None else " WHERE presentation=?")
                + " ORDER BY rowid DESC LIMIT ?",
                (*(() if presentation_id is None else (presentation_id,)), limit),
            ).fetchall()
            return [self._export(db, row) for row in rows]
