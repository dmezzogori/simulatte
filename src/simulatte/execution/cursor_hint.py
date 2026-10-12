"""Committed cursor hints from one sealed segment, never a scan of prior prefixes.

Hints do not authorize playback. Readers still verify each immutable prefix and
its indexes. HEADER and preparation records have no simulation cursor.
"""

from collections.abc import Mapping
import math
import zlib

from simulatte._wire import MAX_SAFE_INT, unpack
from simulatte.trace.format import PREAMBLE, RECORD_HEADER, TRAILER, RecordType
from simulatte.trace.reader import ReaderLimits


def committed_cursor(path, *, first=False, previous=None):
    last = previous

    def consider(value):
        nonlocal last
        if (
            not isinstance(value, (tuple, list))
            or len(value) != 2
            or type(value[0]) not in (int, float)
            or math.isnan(value[0])
            or value[0] < 0
            or type(value[1]) is not int
            or not -1 <= value[1] <= MAX_SAFE_INT
        ):
            raise ValueError("Invalid committed cursor hint")
        cursor = (value[0], value[1])
        last = cursor if last is None else max(last, cursor)

    with path.open("rb") as file:
        end = path.stat().st_size
        if first:
            file.seek(PREAMBLE.size)
        while file.tell() < end:
            header = file.read(RECORD_HEADER.size)
            if len(header) != RECORD_HEADER.size:
                raise ValueError("Incomplete committed record header")
            length, kind, checksum = RECORD_HEADER.unpack(header)
            if length > ReaderLimits().max_record or file.tell() + length > end:
                raise ValueError("Committed record exceeds its segment or reader limit")
            if kind not in (
                RecordType.INITIAL,
                RecordType.INDEX,
                RecordType.OBSERVERS,
                RecordType.KPI,
                RecordType.FOOTER,
            ):
                file.seek(length, 1)
                continue
            raw = file.read(length)
            if zlib.crc32(raw) != checksum:
                raise ValueError("Committed record checksum mismatch")
            body = unpack(raw)
            if not isinstance(body, Mapping):
                raise ValueError("Committed record must be a map")
            if kind in (RecordType.INITIAL, RecordType.FOOTER):
                consider(body.get("cursor"))
            elif kind == RecordType.INDEX or kind == RecordType.OBSERVERS and body.get("phase") == "simulation":
                consider(body.get("last"))
            elif kind == RecordType.KPI:
                for sample in body.get("samples", ()):
                    if not isinstance(sample, (tuple, list)) or len(sample) != 4:
                        raise ValueError("Invalid committed KPI sample")
                    consider((sample[1], sample[0]))
            if kind == RecordType.FOOTER:
                if file.tell() + TRAILER.size != end:
                    raise ValueError("Committed footer must end with one trailer")
                file.seek(TRAILER.size, 1)
    return last
