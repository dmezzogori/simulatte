"""Tier-1 dispatching rules: simple, parameter-free queue-ordering rules.

Each rule here is a small ``(job, server) -> float`` function. Lower
numeric value = served first (matches SimPy's ``PriorityResource``
ascending sort).

Pass any of these to :class:`~simulatte.router.Router` as the
``priority_policies`` argument, or to
:class:`~simulatte.job.ProductionJob` as the ``priority_policy`` argument
for hand-built jobs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import BaseJob
    from simulatte.server import Server


# ----- Processing-time-based -----


def spt(job: BaseJob, server: Server) -> float:
    """Shortest Processing Time at *server*.

    Returns ``job.routing[server]``. Jobs with shorter processing times
    at the candidate server are served first.

    Reference: Conway, Maxwell & Miller (1967), Theory of Scheduling.
    """
    return job.routing[server]


# ----- Due-date-based -----


def edd(job: BaseJob, server: Server) -> float:  # noqa: ARG001
    """Earliest Due Date.

    Returns ``job.due_date``. Jobs with earlier due dates are served
    first. *server* is unused (rule is server-agnostic).
    """
    return job.due_date


def odd(job: BaseJob, server: Server) -> float:
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
    """
    t_r = job.psp_exit_at if job.psp_exit_at is not None else job.created_at
    n_ij = job.servers.index(server) + 1
    r_i = len(job.servers)
    slack_per_op = max(0.0, (job.due_date - t_r) / r_i) if r_i > 0 else 0.0
    return t_r + n_ij * slack_per_op


def modd(job: BaseJob, server: Server) -> float:
    """Modified Operational Due Date at *server*.

    Defined as ``m_ij = max(o_ij, now + p_ij)`` where ``o_ij`` is the
    ODD (see :func:`odd`) and ``p_ij = job.routing[server]``. Switches
    dynamically between ODD-driven dispatching (slack-timing regime,
    when ``o_ij > now + p_ij``) and SPT-driven dispatching (when the
    job is late w.r.t. its operational due date and the SPT term
    dominates).

    Reference: Baker & Kanet (1983), Job shop scheduling with modified
    due dates, *Journal of Operations Management*, 4(1), 11-22.
    https://doi.org/10.1016/0272-6963(83)90022-0
    """
    now = server.env.now
    return max(odd(job, server), now + job.routing[server])


# ----- Slack / ratio-based -----


def cr(job: BaseJob, server: Server) -> float:
    """Critical Ratio at *server*.

    Defined as ``cr_ij = (d_i - now) / sum(p_ij for j in R_i)`` — the
    ratio of the job's slack time to its remaining processing time.
    Lower values (jobs that are running out of slack relative to the
    work left) are served first. ``R_i`` is the set of operations not
    yet completed (i.e. servers not yet exited), including the current
    one.

    Returns ``inf`` if the remaining processing time is zero (defensive;
    a queued job always has at least its current operation pending).

    Reference: Berry & Rao (1975), Critical Ratio Scheduling: An
    Experimental Analysis, *Management Science*, 22(2), 192-201.
    https://doi.org/10.1287/mnsc.22.2.192
    """
    now = server.env.now
    remaining_pt = sum(job.routing[s] for s in job.servers if job.servers_exit_at[s] is None)
    if remaining_pt <= 0:
        return float("inf")
    return (job.due_date - now) / remaining_pt


# ----- FCFS / default -----


def fcfs(job: BaseJob, server: Server) -> float:  # noqa: ARG001
    """First Come First Served.

    Returns ``0.0`` for every job, so the SimPy key tuple's ``time``
    component (entry timestamp) does the tiebreaking. Equivalent to
    the Router's no-rule default but explicit at the call site.
    """
    return 0.0
