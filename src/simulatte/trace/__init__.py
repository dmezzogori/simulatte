"""Trace container (spec §11): :class:`TraceRecorder` writes a run to a trace file, :class:`Trace` reads it."""

from __future__ import annotations

from simulatte.trace.format import RecordType
from simulatte.trace.reader import ChunkInfo, Cursor, KpiPoint, ReaderLimits, Trace, TraceCorrupted, TraceEvent
from simulatte.trace.writer import ChunkLimits, Outcome, TraceRecorder

__all__ = [
    "ChunkInfo",
    "ChunkLimits",
    "Cursor",
    "KpiPoint",
    "Outcome",
    "ReaderLimits",
    "RecordType",
    "Trace",
    "TraceCorrupted",
    "TraceEvent",
    "TraceRecorder",
]
