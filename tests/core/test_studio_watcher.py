"""Watching compares captured membership and bytes, not filename suffixes or mtimes."""

import importlib
import importlib.util
import os

import pytest

from simulatte.execution.snapshot import discover_project


def api():
    assert importlib.util.find_spec("simulatte.studio.watcher"), "manifest-aware watcher missing"
    return importlib.import_module("simulatte.studio.watcher")


def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    (tmp_path / "plant.py").write_text("original")
    (tmp_path / "data.csv").write_text("1,2\n")
    return discover_project(tmp_path / "plant.py", tmp_path)


def test_manifest_scan_tracks_declared_data_and_same_mtime_source(tmp_path):
    m = api()
    watcher = m.ProjectWatcher(project(tmp_path), inputs=("data.csv",))
    baseline = watcher.scan()
    assert watcher.scan() == baseline
    source = tmp_path / "plant.py"
    before = source.stat()
    source.write_text("modified")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    changed = watcher.scan()
    assert changed.source_hash != baseline.source_hash
    assert changed.input_hash == baseline.input_hash
    (tmp_path / "data.csv").write_text("2,3\n")
    assert watcher.scan().input_hash != changed.input_hash


def test_nonmembers_and_owned_store_do_not_change_manifest(tmp_path):
    m = api()
    watcher = m.ProjectWatcher(project(tmp_path))
    baseline = watcher.scan()
    (tmp_path / "untracked.py").write_text("ignored")
    (tmp_path / ".simulatte").mkdir()
    (tmp_path / ".simulatte" / "output").write_text("ignored")
    assert watcher.scan() == baseline
    assert not watcher.accept(1, str(tmp_path / ".simulatte" / "output"))
    assert watcher.accept(2, str(tmp_path / "data.csv"))


def test_include_configuration_is_rediscovered(tmp_path):
    m = api()
    watcher = m.ProjectWatcher(project(tmp_path))
    before = watcher.scan()
    (tmp_path / "extra.txt").write_text("input")
    (tmp_path / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py", "*.txt"]\n')
    after = watcher.scan()
    assert after.source_hash != before.source_hash
    assert after.config_hash != before.config_hash


def test_missing_input_and_permission_failure_are_visible(tmp_path, monkeypatch):
    m = api()
    watcher = m.ProjectWatcher(project(tmp_path), inputs=("data.csv",))
    (tmp_path / "data.csv").unlink()
    with pytest.raises(ValueError, match="missing source/input"):
        watcher.scan()
    assert "data.csv" in watcher.check().error
    (tmp_path / "data.csv").write_text("x")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(m, "_copy", denied)
    with pytest.raises(PermissionError):
        watcher.scan()


def test_self_save_suppression_preserves_simultaneous_source_change(tmp_path):
    import hashlib

    m = api()
    watcher = m.ProjectWatcher(project(tmp_path))
    assert watcher.check() is not None
    raw = b'{"schema_version":"1.0"}\n'
    (tmp_path / "layout.json").write_bytes(raw)
    watcher.acknowledge_layout(hashlib.sha256(raw).hexdigest())
    assert watcher.check() is None
    (tmp_path / "plant.py").write_text("changed")
    change = watcher.check()
    assert change.changed == {"source"}


def test_scan_started_before_own_save_cannot_restore_old_baseline(tmp_path, monkeypatch):
    m = api()
    watcher = m.ProjectWatcher(project(tmp_path))
    original = watcher.scan()
    watcher.check()

    def stale_scan():
        watcher.acknowledge_layout("a" * 64)
        return original

    monkeypatch.setattr(watcher, "scan", stale_scan)
    assert watcher.check() is None
    assert watcher._last.layout_hash == "a" * 64


def test_errors_are_bounded_and_recovery_is_not_suppressed(tmp_path, monkeypatch):
    m = api()
    watcher = m.ProjectWatcher(project(tmp_path))
    watcher.check()
    scan = watcher.scan

    def denied():
        raise PermissionError("x" * 5000)

    monkeypatch.setattr(watcher, "scan", denied)
    error = watcher.check()
    assert error.permission_denied and len(error.error) == 4096
    assert watcher.check() is None
    assert watcher.check(["plant.py"]) is not None
    monkeypatch.setattr(watcher, "scan", scan)
    recovered = watcher.check()
    assert recovered.state is not None and recovered.error is None
    assert not recovered.changed


def test_periodic_rescan_recovers_missed_native_notification(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    watchfiles = pytest.importorskip("watchfiles")

    m = api()
    watcher = m.ProjectWatcher(project(tmp_path))
    clock = [0.0]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def awatch(*paths, **options):
        assert options["debounce"] == 1000 and options["step"] == 250
        assert options["yield_on_timeout"] and not options["ignore_permission_denied"]
        (tmp_path / "plant.py").write_text("missed save")
        clock[0] = 6.0
        yield set()

    monkeypatch.setattr(watchfiles, "awatch", awatch)

    async def run():
        events = watcher.events()
        assert (await anext(events)).state is not None
        change = await anext(events)
        assert change.changed == {"source"} and change.rescan
        watcher.close()
        await events.aclose()

    asyncio.run(run())


def test_git_index_changes_membership_and_metadata_filter(tmp_path):
    import subprocess

    m = api()
    project(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    watcher = m.ProjectWatcher(discover_project(tmp_path / "plant.py", tmp_path))
    before = watcher.scan()
    extra = tmp_path / "extra.txt"
    extra.write_text("new")
    assert watcher.scan() == before
    subprocess.run(["git", "-C", str(tmp_path), "add", "extra.txt"], check=True)
    assert watcher.scan().source_hash != before.source_hash
    assert watcher.accept(1, str(tmp_path / ".git" / "index"))
    assert watcher.accept(1, str(tmp_path / ".git" / "refs" / "heads" / "main"))
    assert not watcher.accept(1, str(tmp_path / ".git" / "objects" / "ignored"))


@pytest.mark.parametrize("polling", [False, True])
def test_native_atomic_save_and_shutdown(tmp_path, monkeypatch, polling):
    import asyncio

    watchfiles = pytest.importorskip("watchfiles")

    m = api()
    watcher = m.ProjectWatcher(project(tmp_path), force_polling=polling)

    real_watch = watchfiles.awatch
    started = asyncio.Event()

    async def observed(*paths, **options):
        async for batch in real_watch(*paths, **options):
            started.set()
            yield batch

    monkeypatch.setattr(watchfiles, "awatch", observed)

    async def run():
        events = watcher.events()
        await anext(events)
        pending = asyncio.create_task(anext(events))
        await asyncio.wait_for(started.wait(), 3)
        replacement = tmp_path / "saving.tmp"
        replacement.write_text("atomic replacement")
        replacement.replace(tmp_path / "plant.py")
        changed = await asyncio.wait_for(pending, 3)
        assert changed.changed == {"source"}
        watcher.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(events), 2)

    asyncio.run(run())


def test_native_watch_error_reports_recovery_without_a_content_change(tmp_path, monkeypatch):
    import asyncio

    watchfiles = pytest.importorskip("watchfiles")

    m = api()
    watcher = m.ProjectWatcher(project(tmp_path), interval=0.01)
    calls = []

    async def watch(*paths, **options):
        calls.append(True)
        if len(calls) == 1:
            raise PermissionError("native watch denied")
        yield set()

    monkeypatch.setattr(watchfiles, "awatch", watch)

    async def run():
        events = watcher.events()
        await anext(events)
        error = await anext(events)
        assert error.permission_denied and error.state is None
        recovered = await anext(events)
        assert recovered.state is not None and recovered.error is None
        assert not recovered.changed
        watcher.close()
        await events.aclose()

    asyncio.run(run())


def test_close_during_initial_scan_drops_late_notification(tmp_path, monkeypatch):
    pytest.importorskip("watchfiles")
    import asyncio
    import threading

    m = api()
    watcher = m.ProjectWatcher(project(tmp_path))
    entered, release = threading.Event(), threading.Event()
    scan = watcher.scan

    def slow():
        entered.set()
        assert release.wait(3)
        return scan()

    monkeypatch.setattr(watcher, "scan", slow)

    async def run():
        events = watcher.events()
        pending = asyncio.create_task(anext(events))
        assert await asyncio.to_thread(entered.wait, 2)
        watcher.close()
        release.set()
        with pytest.raises(StopAsyncIteration):
            await pending
        assert watcher._last is None

    asyncio.run(run())


def test_watcher_reconfiguration_and_bounds(tmp_path):
    from dataclasses import replace

    m = api()
    spec = project(tmp_path)
    for interval in (0, 6, True, float("nan")):
        with pytest.raises(ValueError, match="interval"):
            m.ProjectWatcher(spec, interval=interval)
    watcher = m.ProjectWatcher(spec)
    watcher.acknowledge_layout(None)
    watcher.configure(inputs=("data.csv",))
    assert watcher.scan().input_hash != m.ProjectWatcher(spec).scan().input_hash
    watcher.configure(inputs=(), project=spec)
    with pytest.raises(ValueError, match="roots"):
        watcher.configure(inputs=(), project=replace(spec, root=tmp_path.parent))
    assert not watcher.accept(1, str(tmp_path.parent / "outside.txt"))
    change = watcher.check(paths=["x"] * 300)
    assert change.rescan and len(change.paths) == 256
    (tmp_path / "plant.py").write_text("changed")
    change = watcher.check(paths=["x" * 65537])
    assert change.rescan and change.paths == ()


@pytest.mark.parametrize("declared", [("layout.json",), (".simulatte/run.dat",), ("data.csv",)])
def test_watcher_rejects_conflicting_inputs(tmp_path, declared):
    m = api()
    spec = project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\nexclude=["*.csv"]\n')
    with pytest.raises(ValueError, match="conflict"):
        m.ProjectWatcher(spec, inputs=declared).scan()


def test_watcher_rejects_file_count_and_metadata_size_limits(tmp_path):
    m = api()
    spec = project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\nmax_files=1\n')
    with pytest.raises(ValueError, match="file limit"):
        m.ProjectWatcher(spec).scan()
    with pytest.raises(FileNotFoundError):
        m._file_hash(tmp_path / "missing")
    with (tmp_path / "huge.json").open("wb") as file:
        file.truncate((16 << 20) + 1)
    with pytest.raises(ValueError, match="16 MiB"):
        m._file_hash(tmp_path / "huge.json")


@pytest.mark.parametrize("mode", ["closed", "end", "restart", "close_after_batch"])
def test_watcher_stream_shutdown_and_stale_native_errors(tmp_path, monkeypatch, mode):
    import asyncio

    watchfiles = pytest.importorskip("watchfiles")

    m = api()
    spec = project(tmp_path)
    baseline = m.ProjectWatcher(spec).scan()
    watcher = m.ProjectWatcher(spec, baseline=baseline)
    calls = []

    async def fake(*args, **kwargs):
        calls.append(None)
        if mode == "restart" and len(calls) == 1:
            watcher.configure(inputs=("data.csv",), project=spec)
            raise RuntimeError("obsolete watch error")
        if mode == "close_after_batch":
            watcher.close()
        yield {(2, str(tmp_path / "plant.py"))}

    monkeypatch.setattr(watchfiles, "awatch", fake)
    if mode == "closed":
        watcher.close()

    async def run():
        return [change async for change in watcher.events()]

    changes = asyncio.run(run())
    if mode == "restart":
        assert len(calls) == 2 and len(changes) == 1
        assert changes[0].error is None and "inputs" in changes[0].changed
    else:
        assert changes == []
        assert len(calls) == (0 if mode == "closed" else 1)


def test_watcher_refuses_layout_symlink_escape(tmp_path):
    m = api()
    spec = project(tmp_path)
    (tmp_path / "layout.json").symlink_to(tmp_path.parent / "outside-layout.json")
    with pytest.raises(ValueError, match="escapes"):
        m.ProjectWatcher(spec).scan()


def test_captured_baseline_does_not_swallow_edits_during_inspection(tmp_path):
    from simulatte.execution.snapshot import capture_source, capture_inputs

    m = api()
    spec = project(tmp_path)
    snapshot = capture_inputs(capture_source(spec), ("data.csv",))
    baseline = m.captured_state(snapshot, layout_hash=None)
    watcher = m.ProjectWatcher(spec, inputs=("data.csv",), baseline=baseline)
    assert watcher.scan() == baseline
    (tmp_path / "data.csv").write_text("newer working bytes")
    watcher.configure(inputs=("data.csv",), project=spec, baseline=baseline)
    change = watcher.check()
    assert change.changed == frozenset({"inputs"})
    assert change.state.input_hash != snapshot.inputs.content_hash
