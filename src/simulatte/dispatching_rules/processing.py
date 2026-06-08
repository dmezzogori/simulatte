"""Processing-time and baseline dispatching rules.

Stateless ``(job, server) -> float`` rules that order a queue from local
processing information — or, for first-come-first-served, defer entirely to
arrival order. Lower numeric value = served first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import BaseJob
    from simulatte.server import Server


def shortest_processing_time(job: BaseJob, server: Server) -> float:
    """Shortest Processing Time at *server*.

    Returns ``job.routing[server]``. Jobs with shorter processing times
    at the candidate server are served first.

    Reference: Conway, Maxwell & Miller (1967), Theory of Scheduling.
    """
    return job.routing[server]


def first_come_first_served(job: BaseJob, server: Server) -> float:
    """First Come First Served.

    Returns ``0.0`` for every job, so the SimPy key tuple's ``time``
    component (entry timestamp) does the tiebreaking. Equivalent to
    the Router's no-rule default but explicit at the call site.
    """
    return 0.0
