"""Trace container format (spec §11.1).

A trace file is the magic ``b"SIMTRACE"``, the format version (major, minor) as two big-endian u16, a sequence
of records and, when the trace is complete, a trailer. Each record is framed as::

    length (u32) | type (u8) | crc32 of payload (u32) | payload

with big-endian integers. Payloads are MessagePack wire values (see :mod:`simulatte._wire`); ``CHUNK``
payloads are zlib-compressed MessagePack. The trailer is the offset of the ``FOOTER`` record (u64) followed by
``b"SIMTEND\\0"``.
"""

from __future__ import annotations

import struct
import zlib
from enum import IntEnum
from typing import BinaryIO, Final

__all__ = [
    "FORMAT_MAJOR",
    "FORMAT_MINOR",
    "MAGIC",
    "OPTIONAL_FEATURES",
    "PREAMBLE",
    "RECORD_HEADER",
    "REQUIRED_FEATURES",
    "TRAILER",
    "TRAILER_MAGIC",
    "RecordType",
    "write_preamble",
    "write_record",
    "write_trailer",
]

MAGIC: Final = b"SIMTRACE"
FORMAT_MAJOR: Final = 1
FORMAT_MINOR: Final = 0
TRAILER_MAGIC: Final = b"SIMTEND\0"

PREAMBLE: Final = struct.Struct(">8sHH")
"""Magic and format version at the start of the file."""
RECORD_HEADER: Final = struct.Struct(">IBI")
"""Record framing: payload length, record type, CRC-32 of the payload."""
TRAILER: Final = struct.Struct(">Q8s")
"""Footer offset and trailer magic at the end of a complete file."""

REQUIRED_FEATURES: Final[tuple[str, ...]] = ("wire-v1", "deltas-v1", "chunks-zlib")
"""Features every reader of this version must understand (stored in the header)."""
OPTIONAL_FEATURES: Final[tuple[str, ...]] = ("kpi-declarations-v1",)
"""Features a reader may ignore."""


class RecordType(IntEnum):
    """Record types of the container (spec §11.1)."""

    HEADER = 1
    PRELUDE = 2
    INITIAL = 3
    CATALOG_EXT = 4
    CHUNK = 5
    INDEX = 6
    KPI = 7
    FOOTER = 8


def write_preamble(f: BinaryIO) -> int:
    """Write the magic and the format version; return the number of bytes written."""
    return f.write(PREAMBLE.pack(MAGIC, FORMAT_MAJOR, FORMAT_MINOR))


def write_record(f: BinaryIO, rtype: int, payload: bytes) -> int:
    """Write one framed record; return the number of bytes written (framing included)."""
    header = RECORD_HEADER.pack(len(payload), rtype, zlib.crc32(payload))
    return f.write(header) + f.write(payload)


def write_trailer(f: BinaryIO, footer_offset: int) -> int:
    """Write the trailer pointing at the ``FOOTER`` record at `footer_offset`; return the bytes written."""
    return f.write(TRAILER.pack(footer_offset, TRAILER_MAGIC))
