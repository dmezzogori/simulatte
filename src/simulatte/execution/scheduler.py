"""Replication scheduling over a coordinator's experiment slots (design 4.3, D96, D97, D103).

The store is the durable truth: runs, configurations and replications. A running run
is *activated* in one coordinator session; only activated runs dispatch. Requests are
rebuilt from the stored run, never from user code, and the rebuild is checked against
the run id (the hash of every request's content id) before anything is dispatched.
No model code is imported here.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Any

from simulatte.execution.schema import AttemptKey, ExecutionRequest, Revision, content_id, request_id
from simulatte.execution.store import _ACTIVE, MAX_DISPATCHES, RunStore, _blob, experiment_run_id

# Request fields that differ between the replications of one run; the rest is the stored template.
VARYING = ("layout_hash", "parameters", "seed", "runtime")
REPLICATION_STATES = ("pending", "dispatched", "succeeded", "failed", "lost", "cancelled", "reused")
_FAILED = ("failed", "validation_failed", "disk_full")


def run_status(store: RunStore, run_id: str) -> str:
    with store._transaction() as db:
        row = db.execute("SELECT status FROM experiment_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    return row[0]


def counts(store: RunStore, run_id: str) -> tuple[dict[str, int], list[dict[str, int]]]:
    """Replication counts per state for the run and for each configuration, by index."""
    with store._transaction() as db:
        configurations = db.execute("SELECT COUNT(*) FROM configurations WHERE run=?", (run_id,)).fetchone()[0]
        rows = db.execute(
            "SELECT configuration,status,COUNT(*) FROM replications WHERE run=? GROUP BY configuration,status",
            (run_id,),
        ).fetchall()
    totals = dict.fromkeys(REPLICATION_STATES, 0)
    each = [dict.fromkeys(REPLICATION_STATES, 0) for _ in range(configurations)]
    for configuration, status, count in rows:
        totals[status] += count
        each[configuration][status] = count
    return totals, each


def report(store: RunStore, run_id: str) -> dict[str, Any]:
    """Run state with per-configuration counts; ``mixed_runtime`` flags a configuration whose
    succeeded or reused replications resolved different dependency hashes (design 4.2)."""
    record = store.experiment_run(run_id)
    totals, each = counts(store, run_id)
    hashes: list[set] = [set() for _ in record["configurations"]]
    for item in store.replications(run_id):
        if item["status"] in ("succeeded", "reused"):
            manifest = store.result(item["attempt"]).manifest
            hashes[item["configuration"]].add(manifest["simulation"]["dependencies"]["hash"])
    return {
        "run_id": run_id,
        "experiment_id": record["experiment"],
        "name": store.experiment(record["experiment"])["name"],
        "status": record["status"],
        "counts": totals,
        "configurations": [
            {
                "index": configuration["index"],
                "label": configuration["label"],
                "parameters": configuration["parameters"],
                "counts": each[configuration["index"]],
                "mixed_runtime": len(hashes[configuration["index"]]) > 1,
            }
            for configuration in record["configurations"]
        ],
    }


def runs(store: RunStore, *, limit: int = 20) -> list[dict[str, Any]]:
    """The newest runs with their state and totals."""
    with store._transaction() as db:
        rows = db.execute(
            "SELECT r.id,r.experiment,e.name,r.status,r.created FROM experiment_runs r "
            "JOIN experiments e ON e.id=r.experiment ORDER BY r.created DESC,r.id LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "run_id": row[0],
            "experiment_id": row[1],
            "name": row[2],
            "status": row[3],
            "created": row[4],
            "counts": counts(store, row[0])[0],
        }
        for row in rows
    ]


def cancel_run(store: RunStore, run_id: str) -> list[tuple[int, int, AttemptKey]]:
    """Cancel the run, then its pending and re-queueable lost replications.

    Returns the dispatched replications with their attempt keys; the caller cancels
    those attempts. The run status changes first, so no replication binds afterwards.
    """
    status = run_status(store, run_id)
    if status not in ("running", "paused"):
        raise ValueError(f"Experiment run is {status}")
    store.set_run_status(run_id, "cancelled")
    dispatched = []
    for item in store.replications(run_id):
        configuration, index = item["configuration"], item["index"]
        if item["status"] == "pending" or (item["status"] == "lost" and item["dispatches"] < MAX_DISPATCHES):
            store.set_replication_status(run_id, configuration, index, "cancelled")
        elif item["status"] == "dispatched":
            with store._transaction() as db:
                generation = db.execute("SELECT generation FROM attempts WHERE id=?", (item["attempt"],)).fetchone()[0]
            dispatched.append((configuration, index, AttemptKey(item["request_id"], item["attempt"], generation)))
    return dispatched


@dataclass(slots=True)
class _Run:
    """One activated run: the stored request template and the queue of pending replications."""

    run_id: str
    revision: Revision
    template: dict[str, Any]
    runtimes: dict[str, Any]
    documents: dict[str, Any]
    project: dict[str, Any]
    configurations: list[dict[str, Any]]
    timeout: float | None
    queue: list[tuple[int, int]]

    @classmethod
    def load(cls, record: dict[str, Any], timeout) -> _Run:
        preparation = record["preparation"]
        return cls(
            record["id"],
            Revision(preparation["revision"]["number"], preparation["revision"]["digest"]),
            preparation["request"],
            preparation["runtimes"],
            preparation["documents"],
            preparation["project"],
            record["configurations"],
            timeout,
            [],
        )

    def request(self, configuration: int, seed: int, kind: str) -> ExecutionRequest:
        stored = self.configurations[configuration]
        return ExecutionRequest.from_dict(
            {
                **self.template,
                "layout_hash": stored["layout_hash"],
                "parameters": stored["parameters"],
                "seed": str(seed),
                "runtime": self.runtimes[kind],
            }
        )

    def manifest(self, request: ExecutionRequest, configuration: int) -> dict[str, Any]:
        """The captured preparation that ``Coordinator._preparation`` reads for this request."""
        documents = {**self.documents, "layout_ref": self.configurations[configuration]["layout_ref"]}
        return {"request": request.to_dict(), "documents": documents, "project": self.project}


class Scheduler:
    """Dispatch of activated runs to experiment slots; every method holds the coordinator condition."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.store: RunStore = coordinator.store
        self._runs: dict[str, _Run] = {}

    def activate(self, run_id: str, *, timeout=None) -> None:
        """Start dispatching a running or paused run in this session (start and resume)."""
        coordinator = self.coordinator
        with coordinator._condition:
            record = self.store.experiment_run(run_id)
            if record["status"] not in ("running", "paused") or run_id in self._runs:
                return
            run = _Run.load(record, timeout)
            rows = self.store.replications(run_id)
            requests = {
                (item["configuration"], item["index"], kind): run.request(item["configuration"], item["seed"], kind)
                for item in rows
                for kind in run.runtimes
            }
            # Content ids are not stored per replication: the stored rows must rebuild exactly
            # the requests whose content ids the run id was derived from.
            if experiment_run_id(record["experiment"], map(content_id, requests.values())) != run_id:
                raise ValueError("Stored experiment run does not reproduce its requests")
            for runtime in run.runtimes.values():
                if runtime["trusted_runtime_hash"] != coordinator.runtime.content_hash:
                    raise ValueError("Requested runtime differs from this installation; no automatic provisioning")
            if record["status"] == "paused":
                self.store.set_run_status(run_id, "running")
            with self.store._transaction() as db:
                db.executemany(
                    "INSERT OR IGNORE INTO snapshots VALUES (?,?,?)",
                    [
                        (request_id(request), "preparation", _blob(run.manifest(request, configuration)))
                        for (configuration, _, _), request in requests.items()
                    ],
                )
            for item in rows:
                if item["status"] != "pending":
                    continue
                for kind in sorted(run.runtimes):
                    committed = self.store.committed_by_content(
                        content_id(requests[(item["configuration"], item["index"], kind)])
                    )
                    if committed is not None:
                        self.store.set_replication_status(
                            run_id, item["configuration"], item["index"], "reused", attempt=committed
                        )
                        break
                else:
                    run.queue.append((item["index"], item["configuration"]))
            self._runs[run_id] = run
            self._settle(run_id)
            coordinator._condition.notify_all()

    def next(self, actor):
        """The next pending replication, replication-major, whose worker kind matches the slot."""
        from simulatte.execution.coordinator import _Job

        coordinator = self.coordinator
        with coordinator._condition:
            for run_id, run in list(self._runs.items()):
                skipped = []
                try:
                    while run.queue:
                        index, configuration = heapq.heappop(run.queue)
                        with self.store._transaction() as db:
                            row = self.store._replication(db, run_id, configuration, index)
                        if row["status"] != "pending":
                            continue
                        kind = row["worker_kind"] or actor.kind
                        if kind != actor.kind or kind not in run.runtimes:
                            skipped.append((index, configuration))
                            continue
                        request = run.request(configuration, int(row["seed"]), kind)
                        key = self.store.assign(request, run.revision, slot=actor.slot, pinned=True)
                        try:
                            # Refuses another request on re-dispatch, a run that is not running and a stale key.
                            self.store.bind_replication(run_id, configuration, index, key)
                        except BaseException:
                            self.store.revoke(key, "cancelled", details={"message": "Replication was not bound"})
                            raise
                        try:
                            snapshot, documents = coordinator._preparation(request)
                        except (ValueError, KeyError, OSError) as error:
                            coordinator._record_failure(key, "validation_failed", {"message": str(error)[:4096]})
                            self.on_terminal(key, "validation_failed")
                            continue
                        job = _Job(
                            key, request, snapshot, documents, coordinator._deadline(run.timeout), experiment=run_id
                        )
                        coordinator._emit(
                            "queued",
                            key,
                            {"slot": actor.slot, "run_id": run_id, "configuration": configuration, "index": index},
                        )
                        self._progress(run_id)
                        return job
                finally:
                    for item in skipped:
                        heapq.heappush(run.queue, item)
            return None

    def on_terminal(self, key: AttemptKey, status: str) -> None:
        """Apply an attempt's terminal status to the replication it is still bound to."""
        coordinator = self.coordinator
        with coordinator._condition:
            with self.store._transaction() as db:
                row = db.execute(
                    "SELECT run,configuration,idx,status,dispatches FROM replications WHERE attempt=?",
                    (key.attempt_id,),
                ).fetchone()
            if row is None or row["status"] != "dispatched" or status in _ACTIVE:
                return  # A stale attempt, or an attempt that never became durably terminal (recovery decides).
            run_id, configuration, index = row["run"], row["configuration"], row["idx"]
            # Lost and superseded attempts ended without a result through no fault of the model.
            target = (
                "succeeded"
                if status == "succeeded"
                else "failed"
                if status in _FAILED
                else "cancelled"
                if status == "cancelled"
                else "lost"
            )
            self.store.set_replication_status(run_id, configuration, index, target, attempt=key.attempt_id)
            # D97: a lost attempt is dispatched again, with the same request, at most twice.
            if target == "lost" and row["dispatches"] < MAX_DISPATCHES and run_id in self._runs:
                self.store.set_replication_status(run_id, configuration, index, "pending")
                heapq.heappush(self._runs[run_id].queue, (index, configuration))
                coordinator._condition.notify_all()
            self._settle(run_id)
            self._progress(run_id)

    def release(self, key: AttemptKey) -> None:
        """The coordinator's orderly close stopped ``key``: its replication waits for resume.

        The replication returns to ``pending`` without spending a dispatch (D97 counts
        worker losses, not pauses); ``on_terminal`` then finds it no longer dispatched.
        """
        with self.coordinator._condition:
            with self.store._transaction() as db:
                row = db.execute(
                    "SELECT run,configuration,idx,status FROM replications WHERE attempt=?", (key.attempt_id,)
                ).fetchone()
            if row is not None and row["status"] == "dispatched":
                self.store.set_replication_status(
                    row["run"], row["configuration"], row["idx"], "pending", attempt=key.attempt_id, orderly=True
                )

    def cancel(self, run_id: str) -> None:
        coordinator = self.coordinator
        with coordinator._condition:
            dispatched = cancel_run(self.store, run_id)
            self._runs.pop(run_id, None)
            for configuration, index, key in dispatched:
                if coordinator.cancel(key):
                    self.store.set_replication_status(run_id, configuration, index, "cancelled", attempt=key.attempt_id)
            self._progress(run_id)

    def pause(self) -> None:
        """Pause every activated run (coordinator shutdown); recovery would do the same."""
        with self.coordinator._condition:
            try:
                for run_id in list(self._runs):  # Activated runs are running; others leave the registry.
                    self.store.set_run_status(run_id, "paused")
            finally:
                self._runs.clear()

    def _settle(self, run_id: str) -> None:
        if run_status(self.store, run_id) != "running":
            self._runs.pop(run_id, None)
            return
        totals, _ = counts(self.store, run_id)
        if totals["pending"] or totals["dispatched"]:
            return
        # Lost replications here are terminal: a re-queueable one is pending again.
        self.store.set_run_status(run_id, "completed" if totals["succeeded"] or totals["reused"] else "failed")
        self._runs.pop(run_id, None)
        self.coordinator._condition.notify_all()

    def _progress(self, run_id: str) -> None:
        totals, each = counts(self.store, run_id)
        self.coordinator._emit(
            "experiment_progress",
            None,
            {"run_id": run_id, "status": run_status(self.store, run_id), "counts": totals, "configurations": each},
        )
