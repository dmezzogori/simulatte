"""Durability ordering; Windows API contract is unit-tested on other platforms."""

import ctypes
from pathlib import PureWindowsPath
from types import SimpleNamespace
from typing import Any

import pytest

from simulatte.execution import durability


def test_posix_rename_then_sync_directory(tmp_path, monkeypatch):
    source, destination = tmp_path / "temp", tmp_path / "final"
    source.write_bytes(b"bytes")
    import os

    events = []
    real_replace = os.replace

    def replace(src, dst):
        events.append("rename")
        real_replace(src, dst)

    def sync(fd):
        assert destination.read_bytes() == b"bytes"
        events.append("sync")

    monkeypatch.setattr(
        durability,
        "os",
        SimpleNamespace(
            name="posix",
            replace=replace,
            open=lambda *a: 42,
            fsync=sync,
            close=lambda fd: events.append("close"),
            O_RDONLY=0,
        ),
    )
    durability.replace_durable(source, destination)
    assert events == ["rename", "sync", "close"]
    assert not source.exists()


@pytest.mark.parametrize("unc", [False, True])
@pytest.mark.parametrize("success", [False, True])
def test_windows_rename_uses_write_through_and_unicode_paths(monkeypatch, unc, success):
    calls = []

    class Move:
        def __call__(self, source, destination, flags):
            calls.append((source, destination, flags))
            return int(success)

    class Path:
        def __init__(self, name):
            self.name = name

        def resolve(self):
            return PureWindowsPath(self.name)

    move: Any = Move()
    monkeypatch.setattr(durability, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: SimpleNamespace(MoveFileExW=move), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    root = r"\\host\share" if unc else "C:"
    source: Any = Path(root + r"\é.tmp")
    destination: Any = Path(root + r"\é.final")
    if success:
        durability.replace_durable(source, destination)
    else:
        with pytest.raises(OSError, match="replacement"):
            durability.replace_durable(source, destination)
    prefix = "\\\\?\\UNC\\host\\share" if unc else "\\\\?\\C:"
    assert calls == [(prefix + "\\é.tmp", prefix + "\\é.final", 9)]
    durability.sync_directory(destination)
    assert move.argtypes == [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
