"""Composite dispatching rules.

Rules that combine several scheduling signals — processing time, due-date
slack, machine utilization, downstream queue load — into one priority index.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from simulatte.dispatching_rules.work_content import _work_in_next_queue

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import BaseJob
    from simulatte.server import Server


def raghu_rajendran(
    *,
    utilization: float | None = None,
) -> Callable[[BaseJob, Server], float]:
    """Build a Raghu & Rajendran (RR) dispatching rule.

    Priority index (Raghu & Rajendran 1993):

    ``Z_j = exp(u) * p_j + (s_j / RPT_j) * exp(-u) * p_j + WINQ_j``

    where ``p_j`` is the imminent-operation processing time, ``u`` the current
    machine's utilization, ``s_j = d_j - RPT_j - t`` the raw slack (may be
    negative), ``RPT_j`` the remaining processing time (sum over
    ``unfinished_routing``) and ``WINQ_j`` the work content in the next
    machine's queue. RR is a minimum-``Z`` rule, so the index is returned
    directly (lowest served first, no negation).

    The exponential weighting of the processing-time and due-date terms by the
    machine's own utilization is RR's defining feature: the balance differs
    machine to machine. A negative ``s_j`` (tardy job) lowers ``Z_j``, giving
    tardy jobs strong priority.

    Args:
        utilization: Fixed machine utilization ``u`` override in ``[0, 1]``.
            When ``None`` (default), ``u`` is read live from
            ``server.utilization_rate``; early in a run this is ``~= 0``, where
            ``exp(0) = 1`` degrades the rule gracefully to
            ``p_j + (s_j / RPT_j) * p_j + WINQ_j``.

    Returns:
        A ``(job, server) -> float`` callable yielding ``Z_j``.

    Raises:
        ValueError: If ``utilization`` is given and outside ``[0, 1]``.

    Reference: Raghu & Rajendran (1993), An efficient dynamic dispatching rule
    for scheduling in a job shop, IJPE 32(3), 301-313.
    https://doi.org/10.1016/0925-5273(93)90044-L
    """
    if utilization is not None and not (0.0 <= utilization <= 1.0):
        msg = f"utilization must be in [0, 1], got {utilization}"
        raise ValueError(msg)

    def _rr(job: BaseJob, server: Server) -> float:
        p = job.routing[server]
        u = utilization if utilization is not None else server.utilization_rate
        rpt = sum(job.routing[s] for s in job.unfinished_routing)
        winq = _work_in_next_queue(job, server)
        if rpt <= 0:
            return math.exp(u) * p + winq
        s = job.due_date - rpt - server.env.now
        return math.exp(u) * p + (s / rpt) * math.exp(-u) * p + winq

    return _rr
