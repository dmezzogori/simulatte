"""Initial inspection and recovery share the watcher with prepared input ownership."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict
import sqlite3

from simulatte.execution.coordinator import ExecutionFailure, selected_model, _disk_full
from simulatte.execution.references import read_document
from simulatte.execution.schema import Revision
from simulatte.execution.snapshot import discover_project
from simulatte.execution.store import RevisionConflict, _read
from simulatte.studio.monitor import ProjectMonitor
from simulatte.studio.watcher import ProjectWatcher, InputChange


class ProjectSession:
    def __init__(self, coordinator, *, watcher=None, timeout=None, configure=None):
        self.coordinator = coordinator
        self.watcher = watcher if watcher is not None else ProjectWatcher(coordinator.project)
        self.timeout, self.configure = timeout, configure
        self.status = {"phase": "preparing", "error": None}
        self.metadata = None
        self.monitor = None
        self._monitor_task = None
        self._pending = None
        self._epoch = 0
        self._wake = asyncio.Event()
        self._closed = False
        self._running = False
        self._started = False

    def _status(self, phase, error=None):
        self.status = {"phase": phase, "error": error}
        self.coordinator._emit("studio_state", None, self.status)

    def _pause(self, change):
        coordinator = self.coordinator
        with coordinator._condition:
            for job in (coordinator._current, coordinator._pending):
                if job is not None:
                    coordinator.cancel(job.key)
            if coordinator._fatal is not None:
                return None
            try:
                return coordinator.store.create_revision(
                    {
                        "preparing": True,
                        "observed": None if change.state is None else asdict(change.state),
                        "error": change.error,
                    },
                    revoke_active=True,
                )
            except (OSError, sqlite3.Error) as error:
                coordinator._fatal = ExecutionFailure(
                    None,
                    "disk_full" if _disk_full(error) else "failed",
                    {"message": str(error)[:4096], "index_terminal_state": "not_durable"},
                )
                coordinator._pending = None
                coordinator._condition.notify_all()
                self._status("error", coordinator._fatal.details)
                return None

    def _ingest(self, change):
        if self._closed:
            return
        if self.monitor is not None:
            with self.coordinator._condition:
                self.monitor._ingest(change)
            return
        if change.state is not None and change.changed == {"experiments"}:
            return  # Non-source inputs (D104) never restart preparation.
        self._epoch += 1
        base = self._pause(change)
        if base is None:
            self._pending = None
            return
        # A failed rescan alone is no evidence authorizing an automatic retry.
        # Native paths, a changed valid manifest or first startup can request inspection.
        if change.permission_denied or (change.state is None and self._started and not change.paths):
            self._pending = None
            self._status("error", {"message": change.error})
            return
        self._pending = (self._epoch, base)
        self._wake.set()

    def _discovered_inputs(self):
        """A completed inspection may precede a declared-input capture failure."""
        with self.coordinator.store._transaction() as db:
            rows = db.execute(
                "SELECT details FROM attempts WHERE status='succeeded' ORDER BY generation DESC LIMIT 16"
            ).fetchall()
        for row in rows:
            details = _read(row[0])
            if details.get("kind") == "inspection":
                metadata = read_document(details["document"], "model-info")
                inputs = tuple(selected_model(metadata)["inputs"])
                self.metadata = metadata
                if inputs != self.watcher.inputs:
                    self.watcher.configure(inputs=inputs)
                return

    def _prepare(self, epoch, base):
        project = self.coordinator.project
        project = discover_project(project.root / project.model, project.root, layout=project.layout)

        def revision_changed(revision: Revision):
            if self._closed or epoch != self._epoch:
                raise RevisionConflict("Initial inspection was superseded")

        return self.coordinator.prepare(
            expected=base, timeout=self.timeout, _on_revision=revision_changed, _project=project
        )

    def _install(self):
        self.monitor = ProjectMonitor(self.coordinator, watcher=self.watcher, timeout=self.timeout)
        self._monitor_task = asyncio.create_task(self.monitor._process())

    async def _initialize(self):
        while not self._closed and self.monitor is None:
            await self._wake.wait()
            self._wake.clear()
            pending, self._pending = self._pending, None
            if pending is None:
                continue
            epoch, base = pending
            self._started = True
            self._status("preparing")
            try:
                prepared = await asyncio.to_thread(self._prepare, epoch, base)
                with self.coordinator._condition:
                    if self._closed or epoch != self._epoch:
                        continue
                    initial_error = None
                    try:
                        initial = None if self.configure is None else self.configure(prepared)
                        state = self.coordinator.inputs.bind(prepared, initial=initial)
                    except (ValueError, TypeError) as error:
                        # Invalid startup overrides must remain reviewable, without
                        # silently executing model defaults in their place.
                        initial_error = {"message": str(error)[:4096]}
                        state = self.coordinator.inputs.bind(prepared, initial_error=initial_error["message"])
                    self.metadata = prepared.metadata
                    self._install()
                    if initial_error is not None:
                        self._status("error", initial_error)
                        continue
                    if state.request is not None:
                        self.coordinator.submit(state.request, state.revision, timeout=self.timeout)
                    self._status("ready")
            except Exception as error:
                if not self._closed and epoch == self._epoch:
                    self._status("error", {"message": str(error)[:4096]})
                    with suppress(ValueError, KeyError, OSError):
                        self._discovered_inputs()

    async def rescan(self, *, retry=False, expected=None):
        """Focus checks only observe changes; explicit retry may restart failed inspection."""
        change = await asyncio.to_thread(self.watcher.check, rescan=True)
        if change is None and retry:
            observed = await asyncio.to_thread(self.watcher.scan)
            change = InputChange(observed, frozenset(), rescan=True)
        if expected is not None and self.coordinator.store.current_revision() != expected:
            raise RevisionConflict("Project changed during rescan")
        if change is not None:
            self._ingest(change)

    async def run(self):
        if self._running:
            raise RuntimeError("Project session is already running")
        if self._closed:
            return
        self._running = True
        if self.coordinator.inputs.state is not None:
            self.metadata = self.coordinator.inputs.captured_inputs()[1].metadata
            self._install()
            self._status("ready")
        initialize = asyncio.create_task(self._initialize())
        try:
            async for change in self.watcher.events():
                self._ingest(change)
        finally:
            self.close()
            initialize.cancel()
            with suppress(asyncio.CancelledError):
                await initialize
            if self._monitor_task is not None:
                self._monitor_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._monitor_task
            self._running = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._epoch += 1
        self._pending = None
        self._wake.set()
        self.watcher.close()
        if self.monitor is not None:
            self.monitor.close()
        else:
            with self.coordinator._condition:
                for job in (self.coordinator._current, self.coordinator._pending):
                    if job is not None:
                        self.coordinator.cancel(job.key)
