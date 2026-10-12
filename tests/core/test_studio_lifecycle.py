"""Studio starts watching before inspection and remains usable after invalid source."""

import asyncio


from simulatte.execution.coordinator import Coordinator
from simulatte.studio.lifecycle import ProjectSession
from simulatte.studio.watcher import ProjectWatcher
from tests.execution.test_coordinator import MODEL, project


async def wait_until(predicate):
    async with asyncio.timeout(20):
        while not predicate():
            await asyncio.sleep(0.02)


def test_invalid_initial_source_recovers_without_restarting_server(tmp_path):
    async def run():
        spec = project(tmp_path, "raise RuntimeError('broken model')\n")
        with Coordinator(spec) as coordinator:
            session = ProjectSession(coordinator, watcher=ProjectWatcher(spec, interval=0.1), timeout=10)
            task = asyncio.create_task(session.run())
            try:
                await wait_until(lambda: session.status["phase"] == "error")
                assert coordinator.inputs.state is None
                (spec.root / "plant.py").write_text(MODEL)
                await wait_until(lambda: session.status["phase"] == "ready")
                await wait_until(lambda: bool(list((spec.root / ".simulatte/results").glob("*/run.simtrace"))))
                assert coordinator.inputs.state.request.parameters["n"] == 2
            finally:
                session.close()
                await task

    asyncio.run(run())


def test_missing_declared_input_is_watched_after_initial_inspection(tmp_path):
    async def run():
        spec = project(tmp_path)
        (spec.root / "input.txt").unlink()
        with Coordinator(spec) as coordinator:
            watcher = ProjectWatcher(spec, interval=0.1)
            session = ProjectSession(coordinator, watcher=watcher, timeout=10)
            task = asyncio.create_task(session.run())
            try:
                await wait_until(lambda: session.status["phase"] == "error" and watcher.inputs == ("input.txt",))
                (spec.root / "input.txt").write_text("8")
                await wait_until(lambda: session.status["phase"] == "ready")
                assert coordinator.inputs.state.request is not None
            finally:
                session.close()
                await task

    asyncio.run(run())


def test_source_edit_cancels_hung_initial_inspection(tmp_path):
    async def run():
        spec = project(tmp_path, "while True: pass\n")
        with Coordinator(spec) as coordinator:
            session = ProjectSession(coordinator, watcher=ProjectWatcher(spec, interval=0.1), timeout=15)
            task = asyncio.create_task(session.run())
            try:
                await wait_until(lambda: coordinator._current is not None)
                (spec.root / "plant.py").write_text(MODEL)
                await wait_until(lambda: session.status["phase"] == "ready")
                assert coordinator.inputs.state is not None
            finally:
                session.close()
                await task

    asyncio.run(run())


def test_required_parameters_open_without_auto_run(tmp_path):
    async def run():
        spec = project(tmp_path, MODEL.replace("n: int = 2", "n: int"))
        with Coordinator(spec) as coordinator:
            session = ProjectSession(coordinator, watcher=ProjectWatcher(spec, interval=0.1), timeout=10)
            task = asyncio.create_task(session.run())
            try:
                await wait_until(lambda: session.status["phase"] == "ready")
                assert "n" in coordinator.inputs.state.issues
                assert coordinator.inputs.state.request is None
                assert not list((spec.root / ".simulatte/results").glob("*/run.simtrace"))
            finally:
                session.close()
                await task

    asyncio.run(run())


def test_initial_permission_pause_and_undurable_index_close_admission(tmp_path, monkeypatch):
    import errno
    from types import SimpleNamespace
    from simulatte.execution.coordinator import ExecutionFailure

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        session = ProjectSession(coordinator)
        change = SimpleNamespace(state=None, error="denied", paths=(), permission_denied=True)
        session._ingest(change)
        assert session.status["phase"] == "error" and session._pending is None
        session._started = True
        change.permission_denied = False
        session._ingest(change)
        assert session._pending is None

        def fail(*args, **kwargs):
            raise OSError(errno.ENOSPC, "full")

        monkeypatch.setattr(coordinator.store, "create_revision", fail)
        session._ingest(change)
        assert coordinator._fatal.status == "disk_full"
        assert coordinator._fatal.details["index_terminal_state"] == "not_durable"
        coordinator._fatal = ExecutionFailure(None, "failed", {})
        session._ingest(change)
        assert session._pending is None
        session.close()
        session._ingest(change)
        asyncio.run(session.run())


def test_initial_inspection_callback_fences_closed_session(tmp_path, monkeypatch):
    import pytest
    from simulatte.execution.store import RevisionConflict

    with Coordinator(project(tmp_path)) as coordinator:
        session = ProjectSession(coordinator)
        base = coordinator.store.create_revision({})

        def prepare(**kwargs):
            session.close()
            kwargs["_on_revision"](base)

        monkeypatch.setattr(coordinator, "prepare", prepare)
        with pytest.raises(RevisionConflict):
            session._prepare(0, base)


def test_closed_initial_preparation_cannot_bind_or_schedule(tmp_path, monkeypatch):
    import threading

    async def run():
        with Coordinator(project(tmp_path)) as coordinator:
            session = ProjectSession(coordinator)
            entered, release = threading.Event(), threading.Event()

            def prepare(*args):
                entered.set()
                release.wait(5)
                return object()

            monkeypatch.setattr(session, "_prepare", prepare)
            base = coordinator.store.create_revision({})
            session._pending = (0, base)
            session._wake.set()
            task = asyncio.create_task(session._initialize())
            await asyncio.to_thread(entered.wait, 5)
            session.close()
            release.set()
            await task
            assert coordinator.inputs.state is None

    asyncio.run(run())


def test_session_adopts_bound_inputs_and_rejects_duplicate_run(tmp_path):
    import pytest

    async def run():
        spec = project(tmp_path)
        with Coordinator(spec) as coordinator:
            bound = coordinator.inputs.bind(coordinator.prepare(timeout=10))
            coordinator.wait(coordinator.submit(bound.request, bound.revision, timeout=10))
            session = ProjectSession(coordinator, watcher=ProjectWatcher(spec, interval=0.1))
            session._discovered_inputs()
            session._discovered_inputs()
            task = asyncio.create_task(session.run())
            try:
                await wait_until(lambda: session.status["phase"] == "ready")
                with pytest.raises(RuntimeError, match="already running"):
                    await session.run()
                # Route a real layout observation through the adopted monitor.
                (spec.root / "layout.json").write_text("{")
                await wait_until(lambda: "layout" in coordinator.inputs.state.issues)
            finally:
                session.close()
                await task

    asyncio.run(run())


def test_close_contains_active_initial_inspection(tmp_path):
    async def run():
        spec = project(tmp_path, "while True: pass\n")
        with Coordinator(spec) as coordinator:
            session = ProjectSession(coordinator, watcher=ProjectWatcher(spec, interval=0.1))
            task = asyncio.create_task(session.run())
            await wait_until(lambda: coordinator._current is not None)
            key = coordinator._current.key
            session.close()
            await task
            import pytest
            from simulatte.execution.store import StaleAttempt

            with pytest.raises(StaleAttempt):
                coordinator.store.require_active(key)

    asyncio.run(run())


def test_explicit_rescan_retries_unchanged_failure_and_rejects_a_stale_scan(tmp_path, monkeypatch):
    import pytest
    from simulatte.execution.store import RevisionConflict

    async def run():
        spec = project(tmp_path)
        with Coordinator(spec) as coordinator:
            watcher = ProjectWatcher(spec)
            session = ProjectSession(coordinator, watcher=watcher)
            watcher.check()
            base = coordinator.store.create_revision({"initial": True})
            await session.rescan(expected=base)
            assert session._pending is None
            session._status("error", {"message": "previous inspection failed"})
            await session.rescan(retry=True, expected=base)
            assert session._pending is not None
            session._pending = None
            current = coordinator.store.current_revision()
            original = watcher.check

            def racing(**kwargs):
                coordinator.store.create_revision({"new": True}, expected=current)
                return original(**kwargs)

            monkeypatch.setattr(watcher, "check", racing)
            with pytest.raises(RevisionConflict, match="rescan"):
                await session.rescan(expected=current)
            assert session._pending is None
            session.close()

    asyncio.run(run())
