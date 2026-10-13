"""Bounded watcher-to-coordinator orchestration for one prepared local project."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
import sqlite3

from simulatte.execution.coordinator import ExecutionFailure, _disk_full, selected_model
from simulatte.execution.store import RevisionConflict
from simulatte.studio.watcher import ProjectWatcher, captured_state


class ProjectMonitor:
    def __init__(self, coordinator, *, watcher=None, timeout=None):
        self.coordinator = coordinator
        self.owner = coordinator.inputs
        self.timeout = timeout
        self.watcher = watcher if watcher is not None else ProjectWatcher(coordinator.project)
        self._pending = None
        self._processing = False
        self._closed = False
        self._running = False
        self._sequence = 0
        self._wake = asyncio.Event()
        self._acknowledge()

    @property
    def busy(self):
        return self._processing or self._pending is not None

    def _acknowledge(self):
        state, prepared, captured = self.owner.captured_inputs()
        if "source" in state.issues:
            return
        self.watcher.configure(
            inputs=tuple(selected_model(prepared.metadata)["inputs"]),
            project=prepared.snapshot.source.project,
            baseline=captured_state(prepared.snapshot, layout_hash=captured.digest),
        )

    def _ingest(self, change):
        if self._closed:
            return
        state, prepared, captured = self.owner.captured_inputs()
        baseline = captured_state(prepared.snapshot, layout_hash=captured.digest)
        if change.error is not None and change.permission_denied:
            self._sequence += 1
            self._pause(state.revision, change.error)
            self._pending = None
            self._wake.clear()
            self.coordinator._emit("error", None, {"kind": "watcher", "message": change.error})
            return
        observed = change.state
        source_changed = (
            observed is None
            or "source" in state.issues
            or any(
                getattr(observed, field) != getattr(baseline, field)
                for field in ("source_hash", "input_hash", "config_hash")
            )
        )
        layout_changed = observed is not None and observed.layout_hash != baseline.layout_hash
        if observed is not None and "experiments" in change.changed:
            self.observe_experiments(change)
        if not source_changed and not layout_changed:
            return
        self._sequence += 1
        if source_changed:
            state = self._pause(state.revision, change.error or "Source or declared inputs changed")
            if state is None:
                return
        self._pending = ("source" if source_changed else "layout", state.revision, self._sequence)
        self._wake.set()

    def observe_experiments(self, change):
        """Experiment files and layout variants changed; they never refresh the prepared model (D104).

        Clients are told which project-relative experiment files or layout variants changed so they can
        refresh their lists; nothing runs. A rescan without event paths reports an empty list.
        """
        project = self.watcher.project
        paths = []
        for raw in change.paths:
            path = Path(raw).absolute()
            if path.is_relative_to(project.root):
                name = path.relative_to(project.root).as_posix()
                if name.endswith(".experiment.json") or name in project.layouts:
                    paths.append(name)
        self.coordinator._emit("experiments_changed", None, {"paths": sorted(set(paths))})

    def _pause(self, base, message):
        with self.coordinator._condition:
            try:
                return self.owner.invalidate(base, message)
            except (RevisionConflict, RuntimeError):
                return None  # Newer inputs, shutdown or a prior storage failure already fence it.
            except (OSError, sqlite3.Error) as error:
                self.coordinator._fatal = ExecutionFailure(
                    None,
                    "disk_full" if _disk_full(error) else "failed",
                    {"message": str(error)[:4096], "index_terminal_state": "not_durable"},
                )
                self.coordinator._pending = None
                self.coordinator._condition.notify_all()
                self.coordinator._emit(self.coordinator._fatal.status, None, self.coordinator._fatal.details)
                return None

    async def _process(self):
        while not self._closed:
            await self._wake.wait()
            self._wake.clear()
            work, self._pending = self._pending, None
            if work is None:
                continue
            kind, base, sequence = work
            self._processing = True
            try:
                operation = self.owner.refresh if kind == "source" else self.owner.observe_layout
                await asyncio.to_thread(operation, base, timeout=self.timeout)
                if not self._closed:
                    self._acknowledge()
            except RevisionConflict:
                # A preceding presentation commit can advance the base of the
                # latest queued layout observation. Re-observe its current disk
                # bytes, unless a newer observation or pause superseded it.
                with self.coordinator._condition:
                    state = self.owner.state
                    if (
                        not self._closed
                        and kind == "layout"
                        and sequence == self._sequence
                        and self._pending is None
                        and state.revision != base
                        and self.coordinator.store.current_revision() == state.revision
                    ):
                        self._pending = (kind, state.revision, sequence)
                        self._wake.set()
            except Exception as error:
                self._pause(base, str(error)[:4096])
                self.coordinator._emit("error", None, {"kind": "watcher", "message": str(error)[:4096]})
            finally:
                self._processing = False

    async def run(self):
        if self._running:
            raise RuntimeError("Project monitor is already running")
        if self._closed:
            return
        self._running = True
        processor = asyncio.create_task(self._process())
        try:
            async for change in self.watcher.events():
                with self.coordinator._condition:
                    self._ingest(change)
        finally:
            self.close()
            processor.cancel()
            with suppress(asyncio.CancelledError):
                await processor
            self._running = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._sequence += 1
        self.watcher.close()
        self._pending = None
        self._wake.set()
        if self._processing:
            state = self.owner.state
            self._pause(state.revision, "Project watching stopped")
