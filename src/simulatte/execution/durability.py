"""Publish already-fsynced local files before acknowledging their visibility."""

from __future__ import annotations

import os
from pathlib import Path


def replace_durable(source: Path, destination: Path) -> None:
    """Same-filesystem replacement; no copy/delete fallback or network-FS promise."""
    if os.name == "nt":
        import ctypes

        api = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        move = api.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move.restype = ctypes.c_int32

        def extended(path):
            name = str(path.resolve())
            return "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name

        if not move(extended(source), extended(destination), 0x1 | 0x8):
            raise OSError(getattr(ctypes, "get_last_error")(), "Durable file replacement failed")
        return
    os.replace(source, destination)
    descriptor = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
