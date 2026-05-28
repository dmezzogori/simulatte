"""Due-date-based dispatching rules.

Stateless ``(job, server) -> float`` rules that order a queue by operation
due dates derived from the job's due date. Lower numeric value = served first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import BaseJob
    from simulatte.server import Server


def earliest_due_date(job: BaseJob, server: Server) -> float:  # noqa: ARG001
    """Earliest Due Date.

    Returns ``job.due_date``. Jobs with earlier due dates are served
    first. *server* is unused (rule is server-agnostic).
    """
    return job.due_date


def operational_due_date(job: BaseJob, server: Server) -> float:
    """Operational Due Date at *server*.

    Distributes the planned shop-floor slack across the operations of
    *job*'s routing so each operation gets an interim due date. Defined as

    ``o_ij = t_r + n_ij * max(0, (d_i - t_r) / |R_i|)``

    where ``t_r`` is the job's shop-floor entry time, ``n_ij`` is the
    static routing-step number of *server* (1-indexed), ``d_i`` is the
    due date and ``|R_i|`` is the fixed routing length at shop-floor
    entry.

    For push systems (no PSP), ``t_r = job.created_at``; for pull
    systems, ``t_r = job.psp_exit_at`` (release time to shop floor).

    Reference: Land, Stevenson & Thürer (2014), Integrating load-based
    order release and priority dispatching, IJPR 52(4), 1059-1073.
    https://doi.org/10.1080/00207543.2013.836614

    Note: this rule assumes each server appears at most once in a job's
    routing, so ``.index()`` always finds the correct step. This holds in
    practice because ``ProductionJob.routing`` is a ``dict`` keyed by
    server, which structurally prevents duplicate entries.
    """
    t_r = job.psp_exit_at if job.psp_exit_at is not None else job.created_at
    n_ij = job.servers.index(server) + 1
    r_i = len(job.servers)
    slack_per_op = max(0.0, (job.due_date - t_r) / r_i) if r_i > 0 else 0.0
    return t_r + n_ij * slack_per_op


def modified_operational_due_date(job: BaseJob, server: Server) -> float:
    """Modified Operational Due Date at *server*.

    Defined as ``m_ij = max(o_ij, now + p_ij)`` where ``o_ij`` is the
    ODD (see :func:`~simulatte.dispatching_rules.operational_due_date`) and ``p_ij = job.routing[server]``. Switches
    dynamically between ODD-driven dispatching (slack-timing regime,
    when ``o_ij > now + p_ij``) and SPT-driven dispatching (when the
    job is late w.r.t. its operational due date and the SPT term
    dominates).

    Reference: Baker & Kanet (1983), Job shop scheduling with modified
    due dates, *Journal of Operations Management*, 4(1), 11-22.
    https://doi.org/10.1016/0272-6963(83)90022-0
    """
    now = server.env.now
    return max(operational_due_date(job, server), now + job.routing[server])
