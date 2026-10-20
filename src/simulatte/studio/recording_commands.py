"""Studio ``presentation.diff|record|recordings``: presentation recordings from captured studies (design 9).

``presentation.diff`` shows the target request's differences before anything runs;
``presentation.record`` schedules the recording in the command thread (preflight
refusals answer the command) and a daemon thread waits for it, so its outcome reaches
clients as the coordinator's ``recording_state`` notification
``{attempt_id, original_attempt, mode, status, verified}``. Listing and diffs read the
store or a read-only reader; recording needs a writable project.
"""

from __future__ import annotations

from dataclasses import asdict
import threading

from simulatte.presentations.recording import finish_recording, preview_recording, recordings, start_recording
from simulatte.studio.experiments import _keys

RECORDING_COMMANDS = frozenset(("presentation.diff", "presentation.record", "presentation.recordings"))


class RecordingCommands:
    def __init__(self, service):
        self.service = service
        # Recordings whose waiting thread has not finished, by (original attempt, target request id).
        self._active: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def __call__(self, kind, body):
        if kind not in RECORDING_COMMANDS:
            raise ValueError("Unsupported recording command")
        return getattr(self, "_" + kind.split(".")[1])(body)

    def _store(self):
        store = self.service.store or self.service.reader
        if store is None:
            raise ValueError("Saved runs are unavailable")
        return store

    def _diff(self, body):
        _keys(body, {"attempt_id", "seed"}, {"overrides"})
        preview = preview_recording(
            self._store(), body["attempt_id"], seed=body["seed"], overrides=body.get("overrides")
        )
        return {"ok": True, **preview}

    def _record(self, body):
        _keys(body, {"attempt_id", "seed"}, {"overrides", "worker"})
        worker = body.get("worker")
        if worker is not None and type(worker) is not str:
            raise ValueError("worker must be a worker name")
        self.service._writable()
        co = self.service.ensure_coordinator()
        attempt, seed, overrides = body["attempt_id"], body["seed"], body.get("overrides")
        with self._lock:
            # A repeated request while the same recording is active returns that recording.
            identity = (
                attempt,
                preview_recording(co.store, attempt, seed=seed, overrides=overrides)["target_request_id"],
            )
            if identity in self._active:
                return {"ok": True, "job": self._active[identity]}
            job = start_recording(
                co, attempt, seed=seed, overrides=overrides, worker=worker, timeout=self.service.timeout
            )
            reply = {
                "key": asdict(job.key),
                "original_attempt": job.original_attempt,
                "mode": job.mode,
                "diff": list(job.diff),
            }
            self._active[identity] = reply
        threading.Thread(
            target=self._finish, args=(co, job, identity), name="simulatte-studio-recording", daemon=True
        ).start()
        return {"ok": True, "job": reply}

    def _finish(self, co, job, identity):
        try:
            finish_recording(co, job, timeout=self.service.timeout)
        except Exception:
            pass  # ``finish_recording`` announced the outcome, or the coordinator closed meanwhile.
        finally:
            with self._lock:
                self._active.pop(identity, None)

    def _recordings(self, body):
        _keys(body, set(), {"original_attempt"})
        with self._store()._transaction() as db:
            return {"ok": True, "items": recordings(db, original_attempt=body.get("original_attempt"))}
