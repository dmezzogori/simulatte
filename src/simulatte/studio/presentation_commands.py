"""Studio ``presentation.*`` and ``export.*`` commands over the project's saved presentations (contract R4).

Reads (``presentation.list``/``get``, ``export.list``/``get``) also work on a read-only project through its
``StoreReader``; every other command needs the project writer. Each change queues a notification body,
``presentation_changed {id, revision}`` (``revision`` is ``None`` after deletion) or
``export_state {job_id, presentation_id, state}``; the server's pump drains them with :meth:`changes`.
Export bytes never reach the server: ``export.finish`` records what the client saved.
"""

from __future__ import annotations

from collections import deque
import threading

from simulatte.presentations.schema import ManifestError
from simulatte.presentations.store import PresentationStore, StaleRevision

READS = frozenset(("presentation.list", "presentation.get", "export.list", "export.get"))
PRESENTATION_COMMANDS = READS | frozenset(
    (
        "presentation.create",
        "presentation.revise",
        "presentation.delete",
        "export.admit",
        "export.start",
        "export.finish",
        "export.cancel",
    )
)


class PresentationCommands:
    """Dispatch ``<group>.<name>`` to ``_<group>_<name>``."""

    def __init__(self, service):
        self.service = service
        self._pending: deque = deque(maxlen=1024)
        self._lock = threading.Lock()

    def __call__(self, kind, body):
        if kind not in PRESENTATION_COMMANDS:
            raise ValueError("Unsupported presentation command")
        handler = getattr(self, "_" + kind.replace(".", "_"))
        try:
            return handler(self._store() if kind in READS else self._writable(), body)
        except ManifestError as error:
            return {
                "ok": False,
                "error": {"code": "invalid_manifest", "message": str(error)[:4096], "path": error.path},
            }
        except StaleRevision as error:
            return {"ok": False, "error": {"code": "stale_revision", "message": str(error)}}

    def _store(self):
        store = self.service.store or self.service.reader
        if store is None:
            raise ValueError("Saved presentations are unavailable")
        return PresentationStore(store)

    def _writable(self):
        self.service._writable()
        return PresentationStore(self.service.store)

    def _notify(self, kind, body):
        with self._lock:
            self._pending.append((kind, body))

    def _job(self, job):
        self._notify("export_state", {"job_id": job.id, "presentation_id": job.presentation_id, "state": job.state})
        return {"ok": True, "job": job.to_dict()}

    def watching(self):
        with self._lock:
            return bool(self._pending)

    def changes(self):
        """Notifications queued since the last call, as ``(kind, body)`` pairs in order."""
        with self._lock:
            changes = list(self._pending)
            self._pending.clear()
        return changes

    def _presentation_list(self, store, body):
        return {"ok": True, **store.list(body.get("cursor"), body.get("limit", 50))}

    def _presentation_get(self, store, body):
        identity = body["id"]
        return {
            "ok": True,
            "manifest": store.get(identity, body.get("revision")),
            "revisions": store.revisions(identity),
            "exports": [job.to_dict() for job in store.exports(identity)],
        }

    def _presentation_create(self, store, body):
        identity = store.create(body["manifest"])
        self._notify("presentation_changed", {"id": identity, "revision": 1})
        return {"ok": True, "id": identity, "revision": 1}

    def _presentation_revise(self, store, body):
        revision = store.revise(body["id"], body["expected_revision"], body["manifest"])
        self._notify("presentation_changed", {"id": body["id"], "revision": revision})
        return {"ok": True, "revision": revision}

    def _presentation_delete(self, store, body):
        store.delete(body["id"])
        self._notify("presentation_changed", {"id": body["id"], "revision": None})
        return {"ok": True}

    def _export_admit(self, store, body):
        job = store.admit_export(
            body["presentation_id"], body["revision"], body["target"], body.get("options", {}), body.get("statics", [])
        )
        return self._job(job)

    def _export_start(self, store, body):
        return self._job(store.start_export(body["job_id"]))

    def _export_finish(self, store, body):
        return self._job(store.finish_export(body["job_id"], body["outcome"]))

    def _export_cancel(self, store, body):
        return self._job(store.cancel_export(body["job_id"]))

    def _export_list(self, store, body):
        return {"ok": True, "items": [job.to_dict() for job in store.exports(body.get("presentation_id"))]}

    def _export_get(self, store, body):
        return {"ok": True, "job": store.export(body["job_id"]).to_dict()}
