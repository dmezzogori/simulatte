"""Work-content dispatching rules.

Look-ahead rules that order a queue by the workload a job will encounter at the
next machine on its route. Lower numeric value = served first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import BaseJob
    from simulatte.server import Server


def _next_server_after(job: BaseJob, server: Server) -> Server | None:
    """The server after *server* in *job*'s routing, or ``None`` if last/unrouted.

    Mirrors the equivalent helper in ``focus`` but is kept local so the
    work-content family does not depend on the focus module.
    """
    servers = job.servers
    try:
        idx = servers.index(server)
    except ValueError:
        return None
    if idx + 1 >= len(servers):
        return None
    return servers[idx + 1]


def _work_in_next_queue(job: BaseJob, server: Server) -> float:
    """Total work content queued at the machine after *server* in *job*'s routing.

    Sums the imminent processing time of every job currently waiting in the
    next machine's queue (queue-only: the job in service there is excluded).
    Returns ``0.0`` when *server* is the last operation in the routing, or not
    in it (no downstream machine). Shared with the composite RR rule.
    """
    next_server = _next_server_after(job, server)
    if next_server is None:
        return 0.0
    return sum(q.routing[next_server] for q in next_server.queueing_jobs)


def work_in_next_queue(job: BaseJob, server: Server) -> float:
    """Work In Next Queue (WINQ).

    Returns the total processing time of the jobs waiting in the queue of the
    next machine on *job*'s routing. Jobs whose next machine has less queued
    work are served first, feeding soon-to-starve downstream machines and
    adding look-ahead information that SPT lacks.

    Queue-only convention: excludes the job currently in service at the next
    machine. A job on its last operation has no downstream queue and returns
    ``0.0``.

    Reference: Blackstone, Phillips & Hogg (1982), A state-of-the-art survey of
    dispatching rules for manufacturing job shop operations, IJPR 20(1), 27-45.
    https://doi.org/10.1080/00207548208947745
    """
    return _work_in_next_queue(job, server)
