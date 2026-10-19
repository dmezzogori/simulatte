"""Studio experiment, comparison, replay and worker commands (design 7).

These commands fence on run ids and store state, never on the project revision, and
run without the Studio mutation lock. Definitions are validated against the bound
preparation; no model code is imported here.
"""

from __future__ import annotations

from dataclasses import replace
import json
import heapq
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import threading
import time

from simulatte.execution.coordinator import ExecutionFailure
from simulatte.execution.durability import replace_durable, sync_directory
from simulatte.execution.layout_files import _path, read_layout_file
from simulatte.execution.schema import _plain, request_id
from simulatte.execution.store import _ACTIVE, INTERACTIVE, _read
from simulatte.layout.identity import layout_hash

# 8 recordings x 4 keys x 100 000 points (about 30 bytes each) stay below the 128 MiB HTTP reference cap.
MAX_SERIES_ATTEMPTS = 8
MAX_SERIES_KEYS = 4
MAX_SERIES_POINTS = 100_000
MAX_LISTED = 20
_SUFFIX = ".experiment.json"
_HEX64 = re.compile(r"[a-f0-9]{64}")
_HEX32 = re.compile(r"[a-f0-9]{32}")


def _keys(body, required, optional=frozenset()):
    if not set(required) <= body.keys() or body.keys() - set(required) - set(optional):
        expected = ", ".join(sorted({*required, *optional}))
        raise ValueError(f"Expected only the fields {expected}")


def _identity(value, what, pattern=_HEX64):
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError(f"Invalid {what}")
    return value


def experiment_path(root: Path, name) -> Path:
    """A normalized project-relative ``*.experiment.json`` path without symlink components."""
    if type(name) is not str or len(name.encode("utf-8")) > 1024 or not name.endswith(_SUFFIX):
        raise ValueError("Experiment files are project-relative paths ending in .experiment.json")
    if any(part.startswith(".") for part in PurePosixPath(name).parts):
        raise ValueError("Experiment files cannot be hidden or inside hidden directories")
    try:
        return _path(root, name)
    except ValueError:
        raise ValueError("Experiment path must be normalized, project-relative and free of symlinks") from None


def run_summary(store, run_id: str) -> dict:
    with store._transaction() as db:
        row = db.execute(
            "SELECT r.status,r.experiment,e.name FROM experiment_runs r JOIN experiments e ON e.id=r.experiment "
            "WHERE r.id=?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise KeyError(run_id)
    return {"run_id": run_id, "status": row["status"], "name": row["name"], "experiment_id": row["experiment"]}


def prefix(db, attempt):
    """The newest committed trace prefix of an attempt, served by ``api/prefix/<attempt>/<generation>``."""
    row = db.execute(
        "SELECT sequence,offset+length AS length,prefix_hash FROM receipts "
        "WHERE attempt=? AND stream='trace' ORDER BY sequence DESC LIMIT 1",
        (attempt,),
    ).fetchone()
    if row is None:
        return None
    return {
        "attempt": attempt,
        "generation": row["sequence"],
        "length": row["length"],
        "sha256": row["prefix_hash"],
        "url": f"/api/prefix/{attempt}/{row['sequence']}",
    }


def report(store, run_id: str, *, level=0.95, baseline=None, bonferroni=False, configuration=None) -> dict:
    """The Task 5 report document of one run; ``KeyError`` for an unknown run.

    Each listed replication carries the trace of its committed result (``None`` otherwise),
    so Studio can open it in the viewer, and its request id, so Studio can replay it.
    """
    from simulatte.experiments.results import ExperimentReport

    if type(level) not in (int, float) or not 0 < level < 1:
        raise ValueError("level must be a number between 0 and 1")
    if baseline is not None and type(baseline) is not str:
        raise ValueError("baseline must be a configuration label")
    if type(bonferroni) is not bool:
        raise ValueError("bonferroni must be a boolean")
    document = ExperimentReport(store, _identity(run_id, "run id")).to_dict(
        float(level), baseline=baseline, bonferroni=bonferroni, configuration=configuration
    )
    with store._transaction() as db:
        for item in document["replications"]:
            committed = item["status"] in ("succeeded", "reused") and item["attempt"] is not None
            item["trace"] = prefix(db, item["attempt"]) if committed else None
            row = db.execute("SELECT request_id FROM attempts WHERE id=?", (item["attempt"],)).fetchone()
            item["request_id"] = None if row is None else row[0]
    return document


def replays(db, prefix) -> list[dict]:
    """The newest replays, with the committed trace of each succeeded one."""
    # A preflight failure has no attempt. Its durable operation time must outrank
    # older attempted work, including when retrying an old replay request. Legacy
    # rows retain their attempt-time ordering. Scan the data-only cursor with a
    # bounded heap: neither snapshot payload nor retained rows grow with history.
    rows = heapq.nsmallest(
        MAX_LISTED,
        db.execute(
            "SELECT p.*,a.generation,a.created FROM replays p LEFT JOIN attempts a ON a.id=p.attempt"
        ),
        key=lambda row: (-_read(row["details"]).get("_operation_updated_at", row["created"] or 0), row["request_id"]),
    )
    listed = []
    for row in rows:
        details = _read(row["details"])
        verified = row["verified"]
        listed.append(
            {
                "request_id": row["request_id"],
                "original": row["original"],
                "status": row["status"],
                "verified": None if verified is None else bool(verified),
                "reason": details.get("reason") or details.get("message"),
                "key": None
                if row["attempt"] is None
                else {"request_id": row["request_id"], "attempt_id": row["attempt"], "generation": row["generation"]},
                "trace": prefix(db, row["attempt"]) if row["status"] == "succeeded" else None,
            }
        )
    return listed


class ExperimentCommands:
    """Experiment commands over one coordinator, run in a worker thread by the Studio server."""

    def __init__(self, coordinator, *, timeout=None, workers_error=None):
        self.coordinator = coordinator
        self.timeout = timeout
        self.workers_error = workers_error
        # Replay requests whose thread has not finished; a repeated start returns the running replay.
        self._replaying: set[str] = set()
        self._replaying_lock = threading.Lock()

    def __call__(self, kind: str, body: dict) -> dict:
        return getattr(self, "_" + kind.replace(".", "_"))(body)

    @property
    def store(self):
        return self.coordinator.store

    def _state(self, summary):
        self.coordinator._emit("experiment_state", None, summary)
        return {"ok": True, **summary}

    def _prepared(self):
        owner = self.coordinator._inputs
        prepared = None if owner is None else owner._prepared
        if prepared is None:
            raise ValueError("Wait for model metadata before defining an experiment")
        return prepared

    def _experiment_define(self, body):
        from simulatte.experiments import Experiment, expand, experiment_id

        _keys(body, {"path", "definition"}, {"overwrite"})
        overwrite = body.get("overwrite", False)
        if type(overwrite) is not bool:
            raise ValueError("overwrite must be a boolean")
        root = self.coordinator.project.root
        path = experiment_path(root, body["path"])
        experiment = Experiment.from_dict(body["definition"])
        prepared = self._prepared()
        project = prepared.snapshot.source.project
        layouts = {}
        for variant in experiment.layouts:
            if variant.file == project.layout or variant.file not in project.layouts:
                continue  # expand reports a variant outside [tool.simulatte.bundle] layouts
            captured = read_layout_file(project.root, variant.file)
            if captured.raw is None:
                raise ValueError(f"Layout variant file {variant.file!r} does not exist")
            # Validation only: the expansion is discarded, so no layout document is written.
            layouts[variant.label] = (layout_hash("file", _plain(captured.layer().to_dict())), {})
        expand(experiment, prepared, layouts, {"local": prepared.runtime})
        raw = (json.dumps(body["definition"], ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        self._write(root, body["path"], path, raw, overwrite=overwrite)
        return {"ok": True, "path": body["path"], "experiment_id": experiment_id(experiment)}

    @staticmethod
    def _write(root, name, path, raw, *, overwrite):
        parent = path.parent
        try:
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise ValueError("Experiment directory does not exist")
        except FileNotFoundError:
            raise ValueError("Experiment directory does not exist") from None
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            experiment_path(root, name)  # Again just before publishing: no component became a symlink.
            if not overwrite:
                try:
                    os.link(temporary, path)  # Atomic: never replaces a file created meanwhile.
                except FileExistsError:
                    raise ValueError("Experiment file exists; set overwrite to replace it") from None
                sync_directory(parent)
                return
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError("Experiment path is not a regular file")
            except FileNotFoundError:
                pass
            replace_durable(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _experiment_start(self, body):
        from simulatte.experiments import Experiment, load_experiment

        _keys(body, set(), {"path", "run_id", "workers", "level"})
        if ("path" in body) == ("run_id" in body):
            raise ValueError("experiment.start needs exactly one of path or run_id")
        if "path" in body:
            path = experiment_path(self.coordinator.project.root, body["path"])
            if not path.is_file():
                raise ValueError(f"Experiment file {body['path']!r} does not exist")
            experiment = load_experiment(path)
        else:
            record = self.store.experiment_run(_identity(body["run_id"], "run id"))
            experiment = Experiment.from_dict(self.store.experiment(record["experiment"])["definition"])
        if body.get("level") is not None:
            experiment = replace(experiment, level=body["level"])
        workers = body.get("workers")
        if workers is not None and (type(workers) is not list or any(type(name) is not str for name in workers)):
            raise ValueError("workers must be a list of worker names")
        run_id = self.coordinator.start_experiment(experiment, workers=workers, timeout=self.timeout)
        return self._state(run_summary(self.store, run_id))

    def _run(self, body):
        _keys(body, {"run_id"})
        return _identity(body["run_id"], "run id")

    def _experiment_cancel(self, body):
        run_id = self._run(body)
        self.coordinator.cancel_experiment(run_id)
        return self._state(run_summary(self.store, run_id))

    def _experiment_resume(self, body):
        run_id = self._run(body)
        self.coordinator.resume_experiment(run_id, timeout=self.timeout)
        return self._state(run_summary(self.store, run_id))

    def _experiment_delete(self, body):
        run_id = self._run(body)
        summary = run_summary(self.store, run_id)
        removed = self.coordinator.delete_experiment(run_id)
        return {**self._state({**summary, "status": "deleted"}), "results": len(removed["results"])}

    def _comparison_get(self, body):
        _keys(body, {"run_id"}, {"level", "baseline", "bonferroni", "configuration"})
        return {
            "ok": True,
            "report": report(
                self.store,
                body["run_id"],
                level=body.get("level", 0.95),
                baseline=body.get("baseline"),
                bonferroni=body.get("bonferroni", False),
                configuration=body.get("configuration"),
            ),
        }

    def _series_get(self, body):
        from simulatte.trace import Trace

        _keys(body, {"attempts", "keys"})
        attempts, keys = body["attempts"], body["keys"]
        if type(attempts) is not list or not 1 <= len(attempts) <= MAX_SERIES_ATTEMPTS:
            raise ValueError(f"series.get takes 1 to {MAX_SERIES_ATTEMPTS} recordings")
        if (
            type(keys) is not list
            or not 1 <= len(keys) <= MAX_SERIES_KEYS
            or any(type(key) is not str or len(key) > 1024 for key in keys)
        ):
            raise ValueError(f"series.get takes 1 to {MAX_SERIES_KEYS} KPI keys")
        listed = []
        for attempt in attempts:
            result = self.store.result(_identity(attempt, "attempt id", _HEX32))
            series = Trace.open(result.path / "run.simtrace").kpi_series()
            points = {}
            for key in keys:
                samples = series.get(key, [])
                points[key] = {
                    "points": [[t, seq, value] for (t, seq), value in samples[:MAX_SERIES_POINTS]],
                    "truncated": len(samples) > MAX_SERIES_POINTS,
                }
            listed.append({"attempt": attempt, "series": points})
        return {"ok": True, "series": listed}

    def _replay_start(self, body):
        _keys(body, {"request_id"}, {"worker"})
        identity = _identity(body["request_id"], "request id")
        worker = body.get("worker")
        if worker is not None and type(worker) is not str:
            raise ValueError("worker must be a worker name")
        store = self.store
        with store._transaction() as db:
            # A replay's own request is the full form of its original: replaying it replays the original.
            link = db.execute("SELECT original FROM replays WHERE request_id=?", (identity,)).fetchone()
            original = identity if link is None else link[0]
            committed = db.execute(
                "SELECT 1 FROM results r JOIN attempts a ON a.id=r.attempt WHERE a.request_id=?", (original,)
            ).fetchone()
        replay = request_id(replace(store.request(original), level="full"))
        if committed is None:
            raise ValueError("No committed result to replay")
        outcome = {"ok": True, "request_id": replay, "original": original}
        with self._replaying_lock:
            if replay in self._replaying or self._running(replay):
                return outcome
            self._replaying.add(replay)
        try:
            threading.Thread(
                target=self._replay, args=(original, replay, worker), name="simulatte-studio-replay", daemon=True
            ).start()
        except BaseException:
            with self._replaying_lock:
                self._replaying.discard(replay)
            raise
        return outcome

    def _running(self, replay) -> bool:
        """Whether the replay's row is ``running`` on a live attempt (possibly started by another owner)."""
        with self.store._transaction() as db:
            row = db.execute(
                "SELECT 1 FROM replays p JOIN attempts a ON a.id=p.attempt WHERE p.request_id=? AND p.status='running' "
                f"AND a.status IN ({','.join('?' * len(_ACTIVE))})",
                (replay, *_ACTIVE),
            ).fetchone()
        return row is not None

    def _replay(self, original, replay, worker):
        co = self.coordinator

        def attempt():
            with co.store._transaction() as db:
                row = db.execute("SELECT attempt FROM replays WHERE request_id=?", (replay,)).fetchone()
            return None if row is None else row[0]

        try:
            before = attempt()
            co.replay(original, worker=worker, timeout=self.timeout)
        except Exception as error:
            status = (
                error.status
                if isinstance(error, ExecutionFailure)
                else "validation_failed"
                if isinstance(error, (ValueError, KeyError, TypeError))
                else "failed"
            )
            try:
                # A scheduled replay announced its own outcome; announce only failures before scheduling.
                if co.store.fail_replay_preflight(
                    replay, original, before, {"message": str(error)[:4096], "code": status}
                ):
                    body = {"request_id": replay, "original": original, "status": status, "verified": None}
                    co._emit("replay_state", None, {**body, "message": str(error)[:4096]})
            except Exception:
                pass  # The coordinator closed meanwhile; there is nobody left to tell.
        finally:
            with self._replaying_lock:
                self._replaying.discard(replay)

    def _workers_get(self, body):
        _keys(body, set())
        return {"ok": True, "workers": self.workers()}

    def workers(self) -> dict:
        """Configured workers (name, host, slots) and the state of every experiment slot; no other config."""
        co = self.coordinator
        with co._condition:
            actors = [(name, actor.kind, actor.host, actor.spec) for name, actor in co._actors.items()]
            now = time.monotonic()
            backoff = {
                name: {"until": actor.backoff_until - now, "reason": actor.backoff_reason}
                for name, actor in co._actors.items()
                if actor.backoff_until > now
            }
        specs = {spec.name: spec for spec in co.slots.workers}
        specs.update((spec.name, spec) for _, _, _, spec in actors if spec is not None)
        with co.store._transaction() as db:
            rows = {
                row["name"]: row
                for row in db.execute(
                    "SELECT s.name,s.active,a.status,a.details FROM slots s "
                    "LEFT JOIN attempts a ON s.generation>0 AND a.generation=s.generation"
                )
            }
        slots = []
        for name, kind, host, _ in sorted(actors):
            if name == INTERACTIVE:
                continue
            row = rows.get(name)
            last = None if row is None else row["status"]
            details = {} if row is None or row["details"] is None else _read(row["details"])
            idle = row is None or row["active"] is None
            slots.append(
                {
                    "name": name,
                    "kind": kind,
                    "worker": host,
                    # ``backoff``: idle because the worker was unreachable; ``until`` is seconds remaining.
                    "state": ("backoff" if name in backoff else "idle") if idle else last,
                    "attempt": None if row is None else row["active"],
                    "last_status": last,
                    "last_code": details.get("code") if isinstance(details, dict) else None,
                    "backoff": backoff.get(name),
                }
            )
        return {
            "local": co.slots.local,
            "workers": [
                {"name": spec.name, "host": spec.host, "slots": spec.slots} for _, spec in sorted(specs.items())
            ],
            "slots": slots,
            "error": self.workers_error,
        }


def parse_results_query(query) -> dict:
    """``level``, ``baseline``, ``bonferroni`` and ``configuration`` of ``GET api/experiments/<run>/results``."""
    if set(query) - {"level", "baseline", "bonferroni", "configuration"} or any(
        len(query.getlist(name)) != 1 for name in query
    ):
        raise ValueError("Results accept one level, baseline, bonferroni and configuration each")
    options = {}
    if "level" in query:
        try:
            options["level"] = float(query["level"])
        except ValueError:
            raise ValueError("level must be a number between 0 and 1") from None
        if not math.isfinite(options["level"]):
            raise ValueError("level must be a number between 0 and 1")
    if "baseline" in query:
        options["baseline"] = query["baseline"]
    if "bonferroni" in query:
        if query["bonferroni"] not in ("true", "false"):
            raise ValueError("bonferroni must be true or false")
        options["bonferroni"] = query["bonferroni"] == "true"
    if "configuration" in query:
        if re.fullmatch(r"0|[1-9][0-9]{0,8}", query["configuration"]) is None:
            raise ValueError("configuration must be a configuration index")
        options["configuration"] = int(query["configuration"])
    return options
