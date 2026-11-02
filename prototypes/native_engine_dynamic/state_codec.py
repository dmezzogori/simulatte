"""Read the research NSTA0001 encoding for independent inspection/tests.

The encoder owns no pointers in this representation. Integers and binary64
values are little-endian. Observer state and allocation/layout details are absent.
"""

import struct

HEADER = struct.Struct("<8s7Id7Qd")


def decode(data):
    if len(data) < HEADER.size:
        raise ValueError("truncated native state")
    (
        magic,
        servers,
        cap,
        policy,
        lifecycle,
        jobs,
        operations,
        pending,
        now,
        sequence,
        floor,
        dispatched,
        completed,
        events,
        calendar_count,
        pool_count,
        makespan,
    ) = HEADER.unpack_from(data)
    expected = (
        108
        + 12 * servers
        + 36 * jobs
        + 4 * (jobs + 1)
        + 44 * operations
        + 4 * dispatched
        + 4 * pool_count
        + 24 * calendar_count
    )
    if magic != b"NSTA0001" or len(data) != expected:
        raise ValueError("invalid native state version or length")
    cursor = HEADER.size

    def rows(fmt, count):
        nonlocal cursor
        record = struct.Struct("<" + fmt)
        result = [record.unpack_from(data, cursor + record.size * i) for i in range(count)]
        cursor += record.size * count
        return result

    state = dict(
        servers=servers,
        cap=cap,
        policy=policy,
        lifecycle=lifecycle,
        policy_pending=pending,
        now=now,
        sequence=sequence,
        floor=floor,
        dispatched=dispatched,
        completed=completed,
        events=events,
        makespan=makespan,
    )
    state["resources"] = rows("III", servers)
    state["jobs"] = rows("ddddI", jobs)
    state["offsets"] = [r[0] for r in rows("I", jobs + 1)]
    state["operations"] = rows("IIIdddd", operations)
    state["dispatch"] = [r[0] for r in rows("I", dispatched)]
    state["pool"] = [r[0] for r in rows("I", pool_count)]
    state["calendar"] = rows("dQII", calendar_count)
    assert cursor == len(data)
    return state
