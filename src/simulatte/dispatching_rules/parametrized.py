"""Tier-2 dispatching rules: parameterized factory functions for queue ordering.

Each factory here takes construction-time configuration (typically a
per-operation allowance) and returns a ``(job, server) -> float`` callable.
Lower numeric value = served first.

Pass the returned callable to :class:`~simulatte.router.Router` as the
``priority_policies`` argument, e.g.
``Router(priority_policies=planned_slack_time(allowance=2.0))``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import BaseJob
    from simulatte.server import Server


def planned_slack_time(allowance: float = 0.0) -> Callable[[BaseJob, Server], float]:
    """Build a Planned Slack Time (PST) dispatching rule.

    Defined as

    ``pst_ij = (d_i - now) - sum(p_ik + k for k in R_i_from_j)``

    where ``R_i_from_j`` is the set of operations from *server* through
    the end of the routing and ``k`` is the per-operation queue-time
    allowance. Lower PST = more urgent (the job is closer to being late).

    The returned callable yields ``inf`` if *server* is not in the job's
    routing or has already been exited — making it safe for priority
    comparisons and ``min()`` calls.

    Args:
        allowance: Per-operation queue-time allowance ``k`` (>= 0).
            Defaults to ``0.0``.

    Returns:
        A ``(job, server) -> float`` callable suitable for use as a
        ``priority_policies`` on `Router` or
        ``priority_policy`` on `ProductionJob`.

    Raises:
        ValueError: If ``allowance`` is negative.

    Reference: Land & Gaalman (1998), The performance of workload
    control concepts in job shops: Improving the release method, IJPE
    56-57, 347-364. https://doi.org/10.1016/S0925-5273(98)00052-8
    """
    if allowance < 0:
        msg = f"allowance must be >= 0, got {allowance}"
        raise ValueError(msg)

    def _pst(job: BaseJob, server: Server) -> float:
        value = job.planned_slack_time_at(server, allowance=allowance)
        return float("inf") if value is None else value

    return _pst


def slack_per_remaining_operation(allowance: float = 0.0) -> Callable[[BaseJob, Server], float]:
    """Build a Slack per Remaining Operation (S/OPN) dispatching rule.

    Defined as ``sopn_ij = pst_ij(k) / |R_i|`` where ``pst_ij`` is the
    Planned Slack Time (see :func:`planned_slack_time`) and ``|R_i|`` is
    the count of operations not yet completed (servers not yet exited),
    including the current one. Lower S/OPN = more urgent.

    The returned callable yields ``inf`` if *server* is not in the job's
    routing or has already been exited.

    Args:
        allowance: Per-operation queue-time allowance ``k`` (>= 0),
            forwarded to the underlying PST computation. Defaults to ``0.0``.

    Returns:
        A ``(job, server) -> float`` callable suitable for use as a
        ``priority_policies`` on `Router` or
        ``priority_policy`` on `ProductionJob`.

    Raises:
        ValueError: If ``allowance`` is negative.

    Reference: Kanet (1982), Note—On Anomalies in Dynamic Ratio Type
    Scheduling Rules: A Clarifying Analysis, *Management Science*,
    28(11), 1337-1341. https://doi.org/10.1287/mnsc.28.11.1337
    """
    if allowance < 0:
        msg = f"allowance must be >= 0, got {allowance}"
        raise ValueError(msg)

    def _sopn(job: BaseJob, server: Server) -> float:
        pst = job.planned_slack_time_at(server, allowance=allowance)
        if pst is None:
            return float("inf")
        r_i = sum(1 for s in job.servers if job.servers_exit_at[s] is None)
        return pst / r_i

    return _sopn
