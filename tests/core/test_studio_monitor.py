"""Watcher orchestration coalesces changes without losing captured-byte fences."""

import asyncio
import threading

import pytest

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.schema import AttemptKey
from simulatte.studio.watcher import ProjectWatcher, InputChange
from tests.execution.test_coordinator import project


def test_pending_source_changes_coalesce_to_one_latest_execution(tmp_path, monkeypatch):
    from simulatte.studio.monitor import ProjectMonitor

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        owner.bind(coordinator.prepare(timeout=10))
        watcher = ProjectWatcher(spec, inputs=("input.txt",))
        entered, release = threading.Event(), threading.Event()
        original = owner.refresh
        calls = []

        def refresh(*args, **kwargs):
            calls.append(None)
            if len(calls) == 1:
                entered.set()
                assert release.wait(10)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, "refresh", refresh)

        async def run():
            queue = asyncio.Queue()

            async def events():
                while (change := await queue.get()) is not None:
                    yield change

            monkeypatch.setattr(watcher, "events", events)
            close = watcher.close

            def stop():
                close()
                queue.put_nowait(None)

            monkeypatch.setattr(watcher, "close", stop)
            monitor = ProjectMonitor(coordinator, watcher=watcher, timeout=10)
            task = asyncio.create_task(monitor.run())
            try:
                (spec.root / "input.txt").write_text("6")
                await queue.put(InputChange(watcher.scan(), frozenset({"inputs"})))
                assert await asyncio.to_thread(entered.wait, 5)
                for number in range(7, 12):
                    (spec.root / "input.txt").write_text(str(number))
                    base = owner.state.revision
                    await queue.put(InputChange(watcher.scan(), frozenset({"inputs"})))
                    async with asyncio.timeout(5):
                        while owner.state.revision == base:
                            await asyncio.sleep(0.01)
                release.set()
                async with asyncio.timeout(15):
                    while monitor.busy or owner.state.request is None:
                        await asyncio.sleep(0.01)
                queued = [event for event in coordinator.events() if event["type"] == "queued"]
                result = await asyncio.to_thread(coordinator.wait, AttemptKey(**queued[-1]["key"]), timeout=15)
                assert (result.path / "artifacts/answer.txt").read_text() == "13"
                assert len(calls) == 2
                assert watcher.check() is None
            finally:
                release.set()
                monitor.close()
                await asyncio.wait_for(task, 5)

        asyncio.run(run())


def test_permission_failure_pauses_until_recovery_without_retry_loop(tmp_path, monkeypatch):
    from simulatte.studio.monitor import ProjectMonitor

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        owner.bind(coordinator.prepare(timeout=10))
        watcher = ProjectWatcher(spec)

        async def run():
            queue = asyncio.Queue()

            async def events():
                while (change := await queue.get()) is not None:
                    yield change

            monkeypatch.setattr(watcher, "events", events)
            close = watcher.close

            def stop():
                close()
                queue.put_nowait(None)

            monkeypatch.setattr(watcher, "close", stop)
            monitor = ProjectMonitor(coordinator, watcher=watcher, timeout=10)
            task = asyncio.create_task(monitor.run())
            coordinator.events()
            try:
                old = owner.state.revision
                await queue.put(
                    InputChange(None, frozenset({"unknown"}), error="permission denied", permission_denied=True)
                )
                async with asyncio.timeout(5):
                    while owner.state.revision == old:
                        await asyncio.sleep(0.01)
                assert "source" in owner.state.issues and not monitor.busy
                assert not any(event["type"] == "queued" for event in coordinator.events())
                invalid = owner.state.revision
                await queue.put(InputChange(watcher.scan(), frozenset()))
                async with asyncio.timeout(15):
                    while owner.state.revision == invalid or monitor.busy or owner.state.request is None:
                        await asyncio.sleep(0.01)
                queued = [event for event in coordinator.events() if event["type"] == "queued"]
                result = await asyncio.to_thread(coordinator.wait, AttemptKey(**queued[-1]["key"]), timeout=15)
                assert (result.path / "artifacts/answer.txt").read_text() == "7"
            finally:
                monitor.close()
                await asyncio.wait_for(task, 5)

        asyncio.run(run())


def test_native_watcher_drives_a_fresh_run_and_suppresses_captured_baseline(tmp_path, monkeypatch):
    import watchfiles
    from simulatte.studio.monitor import ProjectMonitor

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        initial = owner.bind(coordinator.prepare(timeout=10))

        async def run():
            started = asyncio.Event()
            original = watchfiles.awatch

            async def ready(*args, **kwargs):
                async for batch in original(*args, **kwargs):
                    started.set()
                    yield batch

            monkeypatch.setattr(watchfiles, "awatch", ready)
            monitor = ProjectMonitor(coordinator, timeout=10)
            task = asyncio.create_task(monitor.run())
            try:
                await asyncio.wait_for(started.wait(), 5)
                assert owner.state == initial
                monitor._ingest(InputChange(monitor.watcher.scan(), frozenset({"layout"})))
                assert owner.state == initial and not monitor.busy
                coordinator.events()
                (spec.root / "input.txt").write_text("21")
                async with asyncio.timeout(20):
                    while owner.state.revision == initial.revision or monitor.busy or owner.state.request is None:
                        await asyncio.sleep(0.01)
                queued = [event for event in coordinator.events() if event["type"] == "queued"]
                result = await asyncio.to_thread(coordinator.wait, AttemptKey(**queued[-1]["key"]), timeout=15)
                assert (result.path / "artifacts/answer.txt").read_text() == "23"
                assert monitor.watcher.check() is None
            finally:
                monitor.close()
                await asyncio.wait_for(task, 5)

        asyncio.run(run())


def test_removed_input_declaration_recovers_from_old_watcher_manifest_error(tmp_path):
    from simulatte.studio.monitor import ProjectMonitor
    from tests.execution.test_coordinator import MODEL

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        initial = owner.bind(coordinator.prepare(timeout=10))
        monitor = ProjectMonitor(coordinator, timeout=10)
        (spec.root / "input.txt").unlink()
        (spec.root / "plant.py").write_text(
            MODEL.replace('inputs=["input.txt"]', "inputs=[]").replace('int(Path("input.txt").read_text())', "0")
        )

        async def run():
            task = asyncio.create_task(monitor.run())
            try:
                async with asyncio.timeout(20):
                    while owner.state.revision == initial.revision or monitor.busy or owner.state.request is None:
                        await asyncio.sleep(0.01)
                queued = [event for event in coordinator.events() if event["type"] == "queued"]
                result = await asyncio.to_thread(coordinator.wait, AttemptKey(**queued[-1]["key"]), timeout=15)
                assert (result.path / "artifacts/answer.txt").read_text() == "2"
                assert monitor.watcher.inputs == ()
                assert monitor.watcher.check() is None
            finally:
                monitor.close()
                await asyncio.wait_for(task, 5)

        asyncio.run(run())


def test_monitor_close_fences_a_refresh_that_finishes_late(tmp_path, monkeypatch):
    import pytest
    from simulatte.studio.monitor import ProjectMonitor

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        owner.bind(coordinator.prepare(timeout=10))
        coordinator.events()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = owner.refresh

        def blocked(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            try:
                return original(*args, **kwargs)
            finally:
                finished.set()

        monkeypatch.setattr(owner, "refresh", blocked)
        watcher = ProjectWatcher(spec)

        async def run():
            queue = asyncio.Queue()

            async def events():
                while (change := await queue.get()) is not None:
                    yield change

            monkeypatch.setattr(watcher, "events", events)
            original_close = watcher.close

            def close():
                original_close()
                queue.put_nowait(None)

            monkeypatch.setattr(watcher, "close", close)
            monitor = ProjectMonitor(coordinator, watcher=watcher, timeout=10)
            task = asyncio.create_task(monitor.run())
            try:
                (spec.root / "input.txt").write_text("8")
                await queue.put(InputChange(watcher.scan(), frozenset({"inputs"})))
                assert await asyncio.to_thread(entered.wait, 5)
                with pytest.raises(RuntimeError, match="already running"):
                    await monitor.run()
                monitor.close()
                await asyncio.wait_for(task, 5)
                release.set()
                assert await asyncio.to_thread(finished.wait, 5)
                assert owner.state.request is None
                assert not any(event["type"] == "queued" for event in coordinator.events())
                await monitor.run()
            finally:
                release.set()
                monitor.close()
                await asyncio.wait_for(task, 5)

        asyncio.run(run())


def test_layout_permission_race_in_background_processing_pauses_inputs(tmp_path, monkeypatch):
    from simulatte.execution import inputs
    from simulatte.studio.monitor import ProjectMonitor

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        key = coordinator.store.assign(state.request, state.revision)
        monitor = ProjectMonitor(coordinator, timeout=10)
        (spec.root / spec.layout).write_text('{"schema_version":"1.0","grid":{"dx":2}}')
        change = monitor.watcher.check()

        def denied(*args, **kwargs):
            raise PermissionError("layout became unreadable")

        monkeypatch.setattr(inputs, "read_layout_file", denied)

        async def run():
            processor = asyncio.create_task(monitor._process())
            try:
                monitor._ingest(change)
                async with asyncio.timeout(5):
                    while monitor.busy:
                        await asyncio.sleep(0.01)
                assert owner.state.request is None
                assert coordinator.store.attempt(key)["status"] == "superseded"
                assert any(event["type"] == "error" for event in coordinator.events())
            finally:
                monitor.close()
                processor.cancel()
                try:
                    await processor
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())


@pytest.mark.parametrize("disk_full", [True, False])
def test_monitor_index_failure_discards_queued_admission_and_reports_undurable_state(tmp_path, monkeypatch, disk_full):
    import errno
    from simulatte.studio.monitor import ProjectMonitor

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        monitor = ProjectMonitor(coordinator, timeout=10)
        (spec.root / "input.txt").write_text("9")
        change = monitor.watcher.check()

        def full(*args, **kwargs):
            raise OSError(errno.ENOSPC if disk_full else errno.EIO, "index unavailable")

        monkeypatch.setattr(coordinator.store, "create_revision", full)
        with coordinator._condition:
            coordinator.submit(state.request, state.revision, timeout=10)
            monitor._ingest(change)
            assert coordinator._pending is None
            assert coordinator._fatal.status == ("disk_full" if disk_full else "failed")
            assert coordinator._fatal.details["index_terminal_state"] == "not_durable"
            assert not monitor.busy
        failures = [
            event for event in coordinator.events() if event["type"] == ("disk_full" if disk_full else "failed")
        ]
        assert failures and failures[-1]["body"]["index_terminal_state"] == "not_durable"
        monitor.close()


def test_latest_layout_observation_survives_previous_view_revision_commit(tmp_path, monkeypatch):
    import json
    from simulatte.execution import inputs
    from simulatte.studio.monitor import ProjectMonitor
    from tests.execution.test_inputs import PRESENTATION_MODEL

    spec = project(tmp_path, PRESENTATION_MODEL)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        key = coordinator.submit(state.request, state.revision, timeout=10)
        coordinator.wait(key, timeout=15)
        entered, release = threading.Event(), threading.Event()
        original = inputs.reresolve_presentation
        calls = []

        def slow(*args):
            calls.append(None)
            if len(calls) == 1:
                entered.set()
                assert release.wait(10)
            return original(*args)

        monkeypatch.setattr(inputs, "reresolve_presentation", slow)
        watcher = ProjectWatcher(spec)

        async def run():
            monitor = ProjectMonitor(coordinator, watcher=watcher, timeout=10)
            processor = asyncio.create_task(monitor._process())
            path = spec.root / spec.layout

            def changed(color):
                patch = {
                    "schema_version": "1.0",
                    "placements": {"machine": {"kind": "server", "visual": {"color": color}}},
                }
                path.write_text(json.dumps(patch))
                monitor._ingest(InputChange(watcher.scan(), frozenset({"layout"})))
                return patch

            try:
                changed("red")
                assert await asyncio.to_thread(entered.wait, 5)
                latest = changed("blue")
                release.set()
                async with asyncio.timeout(10):
                    while monitor.busy:
                        await asyncio.sleep(0.01)
                _, _, captured = owner.captured_inputs()
                assert captured.layer().to_dict() == latest
                assert coordinator.store.presentation_view(revision=owner.state.revision).key == key
                assert watcher.check() is None
            finally:
                release.set()
                monitor.close()
                processor.cancel()
                try:
                    await processor
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())


def test_monitor_requires_metadata_and_closed_monitor_ignores_late_changes(tmp_path):
    import pytest
    from simulatte.studio.monitor import ProjectMonitor

    with Coordinator(project(tmp_path)) as coordinator:
        with pytest.raises(RuntimeError, match="metadata must be prepared"):
            ProjectMonitor(coordinator)
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        paused = owner.invalidate(state.revision, "waiting for readable source")
        monitor = ProjectMonitor(coordinator)
        monitor.close()
        monitor._ingest(InputChange(None, frozenset({"unknown"}), error="late"))
        asyncio.run(monitor.run())
        assert owner.state == paused and not monitor.busy


def test_failed_refresh_keeps_last_result_and_later_source_edit_recovers(tmp_path):
    from simulatte.studio.monitor import ProjectMonitor
    from tests.execution.test_coordinator import MODEL

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        initial = owner.bind(coordinator.prepare(timeout=10))
        original_key = coordinator.submit(initial.request, initial.revision, timeout=10)
        coordinator.wait(original_key, timeout=15)
        monitor = ProjectMonitor(coordinator, timeout=10)

        async def run():
            processor = asyncio.create_task(monitor._process())
            try:
                (spec.root / "plant.py").write_text("invalid Python !!")
                monitor._ingest(monitor.watcher.check())
                async with asyncio.timeout(15):
                    while monitor.busy:
                        await asyncio.sleep(0.01)
                assert owner.state.request is None and "source" in owner.state.issues
                assert coordinator.store.presentation_view(committed_only=True).key == original_key
                assert any(event["type"] == "error" for event in coordinator.events())
                (spec.root / "plant.py").write_text(MODEL)
                monitor._ingest(monitor.watcher.check())
                async with asyncio.timeout(15):
                    while monitor.busy:
                        await asyncio.sleep(0.01)
                queued = [event for event in coordinator.events() if event["type"] == "queued"]
                result = await asyncio.to_thread(coordinator.wait, AttemptKey(**queued[-1]["key"]), timeout=15)
                assert result.key != original_key
                assert not owner.state.issues
            finally:
                monitor.close()
                processor.cancel()
                try:
                    await processor
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())


def test_permission_pause_wins_over_a_woken_but_unconsumed_refresh(tmp_path):
    from simulatte.studio.monitor import ProjectMonitor

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        owner.bind(coordinator.prepare(timeout=10))
        coordinator.events()
        monitor = ProjectMonitor(coordinator)

        async def run():
            processor = asyncio.create_task(monitor._process())
            try:
                await asyncio.sleep(0)
                (spec.root / "input.txt").write_text("17")
                monitor._ingest(monitor.watcher.check())
                monitor._ingest(
                    InputChange(None, frozenset({"unknown"}), error="permission denied", permission_denied=True)
                )
                await asyncio.sleep(0)
                assert not monitor.busy and owner.state.request is None
                assert not any(event["type"] == "queued" for event in coordinator.events())
            finally:
                monitor.close()
                await asyncio.wait_for(processor, 5)

        asyncio.run(run())
