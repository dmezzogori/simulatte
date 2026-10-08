"""Trace container and recorder (spec §11): :class:`TraceRecorder` writes a run to a trace file."""

from __future__ import annotations

from simulatte.trace.format import RecordType
from simulatte.trace.writer import ChunkLimits, Outcome, TraceRecorder

__all__ = ["ChunkLimits", "Outcome", "RecordType", "TraceRecorder"]
