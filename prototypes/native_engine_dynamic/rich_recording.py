"""Rich prototype source journal, explicitly distinct from the final .simtrace."""

import math

from prototypes.native_engine.recording import RECORD, UNSET, Writer as BaseWriter

MAGIC = b"NTRC0001"
REGISTER, GRANT, DEPART = 7, 8, 9


class Writer(BaseWriter):
    def __init__(self, path):
        self.stream = open(path, "wb", buffering=65536)
        self.stream.write(MAGIC)
        self.buffer, self.count = bytearray(), 0


class Reader:
    def __init__(self, path):
        self.stream = open(path, "rb")
        self.header = False
        self.count, self.previous = 0, 0.0

    def take(self, *, final=False):
        if not self.header:
            magic = self.stream.read(8)
            if not magic and not final:
                return
            if magic != MAGIC:
                raise ValueError("invalid rich recording header")
            self.header = True
        while data := self.stream.read(4096 * RECORD.size):
            if len(data) % RECORD.size:
                raise ValueError("truncated rich source record")
            for row in RECORD.iter_unpack(data):
                now, sequence, kind, job, op, server = row
                if (
                    not math.isfinite(now)
                    or now < self.previous
                    or sequence != self.count
                    or kind not in range(1, 10)
                    or job == UNSET
                ):
                    raise ValueError("invalid rich source time, sequence, kind or job")
                if kind in (1, 5, 6, REGISTER):
                    if op != UNSET or server != UNSET:
                        raise ValueError("invalid rich job fields")
                elif op == UNSET or server == UNSET:
                    raise ValueError("invalid rich operation fields")
                self.previous, self.count = now, self.count + 1
                yield row

    def close(self):
        self.stream.close()


def read_records(path):
    reader = Reader(path)
    try:
        yield from reader.take(final=True)
    finally:
        reader.close()


def validate_records(path, workload, result, *, cap, policy):
    """Independent journal replay, preserving reservation through END until DEPART."""
    owners, local, phases, stages, queues = (
        [],
        [],
        [],
        [None] * len(workload.jobs),
        [[] for _ in range(workload.servers)],
    )
    offsets = [0]
    for job, data in enumerate(workload.jobs):
        for index in range(len(data.route)):
            owners.append(job)
            local.append(index)
            phases.append("new")
        offsets.append(len(owners))
    occupied = [None] * workload.servers
    grants, floor, registered, count = [], 0, 0, 0
    for now, sequence, kind, job, op, server in read_records(path):
        assert job < len(stages)
        data = workload.jobs[job]
        count = sequence + 1
        if kind == REGISTER:
            assert job == registered and stages[job] is None and now <= data.arrival
            stages[job] = "scheduled"
            registered += 1
        elif kind == 1:
            assert stages[job] == "scheduled" and now == data.arrival
            stages[job] = "pool"
        elif kind == 6:
            assert stages[job] == "pool" and now == result["released"][job]
            stages[job] = "active"
            floor += 1
            assert cap is None or floor <= cap
        elif kind == 5:
            assert stages[job] == "active" and now == result["finished"][job]
            assert all(p == "departed" for p in phases[offsets[job] : offsets[job + 1]])
            stages[job] = "finished"
            floor -= 1
        else:
            assert op < len(owners) and owners[op] == job and server == data.route[local[op]]
            assert stages[job] == "active"
            if kind == 2:
                assert phases[op] == "new"
                phases[op] = "queued"
                queues[server].append(op)
            elif kind == GRANT:
                assert occupied[server] is None and phases[op] == "queued"
                expected = (
                    queues[server][0]
                    if policy == "fifo"
                    else min(queues[server], key=lambda i: workload.jobs[owners[i]].durations[local[i]])
                )
                assert op == expected
                queues[server].remove(op)
                occupied[server], phases[op] = op, "granted"
            elif kind == 3:
                assert occupied[server] == op and phases[op] == "granted" and now == result["starts"][op]
                phases[op] = "running"
                grants.append(op)
            elif kind == 4:
                assert occupied[server] == op and phases[op] == "running" and now == result["ends"][op]
                phases[op] = "ended"
            else:
                assert occupied[server] == op and phases[op] == "ended" and now == result["ends"][op]
                occupied[server], phases[op] = None, "departed"
    assert count == result["records"] == 4 * len(stages) + 5 * len(phases)
    assert stages == ["finished"] * len(stages) and phases == ["departed"] * len(phases)
    assert grants == result["dispatch"] and floor == 0 and occupied == [None] * workload.servers
    assert all(not queue for queue in queues)
