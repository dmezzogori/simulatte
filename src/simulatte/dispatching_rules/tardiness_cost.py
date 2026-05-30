"""Tardiness-cost dispatching rules (ATC, COVERT).

Index rules that estimate a job's marginal tardiness cost per unit of imminent
processing time. Both are parameterized factories; call with a look-ahead
parameter to obtain the ``(job, server) -> float`` callable. The returned
callable yields the *negated* priority index, because simulatte serves the
lowest key first while these indices rank the most urgent job highest.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import BaseJob
    from simulatte.server import Server


def apparent_tardiness_cost(
    lookahead: float,
    *,
    avg_processing: float | None = None,
    weight: Callable[[BaseJob], float] | None = None,
) -> Callable[[BaseJob, Server], float]:
    """Build an Apparent Tardiness Cost (ATC) dispatching rule.

    Priority index (Vepsäläinen & Morton 1987):

    ``I_j = (w_j / p_j) * exp(-max(0, d_j - p_j - t) / (k * p_bar))``

    where ``p_j`` is the imminent-operation processing time, ``d_j`` the due
    date, ``t`` the current time, ``w_j`` the job weight, ``k`` the look-ahead
    (scaling) parameter and ``p_bar`` the average processing time of the jobs
    queued at the machine. Higher ``I_j`` = more urgent; the returned callable
    yields ``-I_j`` so the lowest key is served first.

    The slack uses the imminent operation (``d_j - p_j - t``), the canonical
    single-machine Vepsäläinen-Morton form (not a remaining-work or operational
    due-date variant).

    Args:
        lookahead: Scaling parameter ``k`` (> 0). Vepsäläinen & Morton suggest
            roughly 1.5-4.5 when slack is tight.
        avg_processing: Fixed ``p_bar`` override. When ``None`` (default),
            ``p_bar`` is computed live as the mean imminent processing time of
            the jobs queued at the server, falling back to ``p_j`` when the
            queue is empty or that mean is non-positive.
        weight: Optional ``job -> weight`` callable. When ``None``, ``w_j = 1``.

    Returns:
        A ``(job, server) -> float`` callable yielding ``-I_j``.

    Raises:
        ValueError: If ``lookahead <= 0``, or ``avg_processing`` is given and
            ``<= 0``.

    Reference: Vepsäläinen & Morton (1987), Priority rules for job shops with
    weighted tardiness costs, Management Science 33(8), 1035-1047.
    https://doi.org/10.1287/mnsc.33.8.1035
    """
    if lookahead <= 0:
        msg = f"lookahead must be > 0, got {lookahead}"
        raise ValueError(msg)
    if avg_processing is not None and avg_processing <= 0:
        msg = f"avg_processing must be > 0, got {avg_processing}"
        raise ValueError(msg)

    def _atc(job: BaseJob, server: Server) -> float:
        p = job.routing[server]
        if p <= 0:
            return float("-inf")
        w = weight(job) if weight is not None else 1.0
        if avg_processing is not None:
            p_bar = avg_processing
        else:
            queued = [q.routing[server] for q in server.queueing_jobs]
            p_bar = sum(queued) / len(queued) if queued else p
            if p_bar <= 0:
                p_bar = p
        slack = max(0.0, job.due_date - p - server.env.now)
        index = (w / p) * math.exp(-slack / (lookahead * p_bar))
        return -index

    return _atc


def cost_over_time(
    lookahead: float,
    *,
    weight: Callable[[BaseJob], float] | None = None,
) -> Callable[[BaseJob, Server], float]:
    """Build a Cost Over Time (COVERT) dispatching rule.

    Priority index:

    ``C_j = w_j * max(0, 1 - max(0, d_j - t - RPT_j) / (k * RPT_j)) / p_j``

    where ``RPT_j`` is the remaining processing time (sum over
    ``unfinished_routing``, including the current operation), ``p_j`` the
    imminent-operation processing time, ``d_j`` the due date, ``t`` the current
    time and ``k`` the look-ahead parameter. Higher ``C_j`` = more urgent; the
    returned callable yields ``-C_j``.

    Denominator ``k * RPT_j`` is the remaining-work waiting allowance (job-shop
    convention; the single-machine variant uses ``k * p_j``). When the job is
    tardy or just-in-time (slack <= 0) the rule reduces to a WSPT-like
    ``w_j / p_j``; when slack >= ``k * RPT_j`` the cost is ``0``.

    Args:
        lookahead: Look-ahead parameter ``k`` (> 0).
        weight: Optional ``job -> weight`` callable. When ``None``, ``w_j = 1``.

    Returns:
        A ``(job, server) -> float`` callable yielding ``-C_j``.

    Raises:
        ValueError: If ``lookahead <= 0``.

    Reference: Carroll (1965), Heuristic sequencing of single and multiple
    component jobs (PhD thesis, MIT). Job-shop form: Russell, Dar-El & Taylor
    (1987), A comparative analysis of the COVERT job sequencing rule using
    various shop performance measures, IJPR 25(10), 1523-1540.
    """
    if lookahead <= 0:
        msg = f"lookahead must be > 0, got {lookahead}"
        raise ValueError(msg)

    def _covert(job: BaseJob, server: Server) -> float:
        p = job.routing[server]
        if p <= 0:
            return float("-inf")
        w = weight(job) if weight is not None else 1.0
        rpt = sum(job.routing[s] for s in job.unfinished_routing)
        if rpt <= 0:
            return 0.0
        slack = max(0.0, job.due_date - server.env.now - rpt)
        cost = w * max(0.0, 1.0 - slack / (lookahead * rpt)) / p
        return -cost

    return _covert
