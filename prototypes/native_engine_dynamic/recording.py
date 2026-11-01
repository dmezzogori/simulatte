"""Explicitly separate dynamic prototype records, not Studio trace encoding."""

import math

from prototypes.native_engine.recording import RECORD, UNSET, Writer as StaticWriter

MAGIC = b"NDYN0001"
ARRIVAL, QUEUE, START, END, FINISH, RELEASE = range(1, 7)


class Writer(StaticWriter):
    def __init__(self, path):
        self.stream = open(path, "wb", buffering=65536)
        self.stream.write(MAGIC)
        self.buffer = bytearray()
        self.count = 0


def read_records(path):
    with open(path, "rb") as stream:
        if stream.read(8) != MAGIC:
            raise ValueError("invalid dynamic recording header")
        ordinal, previous = 0, 0.0
        while data := stream.read(RECORD.size):
            if len(data) != RECORD.size:
                raise ValueError("truncated dynamic recording")
            row = RECORD.unpack(data)
            now, sequence, kind, _, op, server = row
            if not math.isfinite(now) or now < previous or sequence != ordinal or kind not in range(1, 7):
                raise ValueError("invalid dynamic record time, sequence or kind")
            if kind in (ARRIVAL, RELEASE, FINISH):
                if op != UNSET or server != UNSET:
                    raise ValueError("invalid dynamic job record fields")
            elif op == UNSET or server == UNSET:
                raise ValueError("invalid dynamic operation fields")
            yield row
            previous, ordinal = now, ordinal + 1
