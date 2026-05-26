"""Tier-2 dispatching rules: parameterized callable classes for queue ordering.

Each class here is a ``(job, server) -> float`` callable with
construction-time configuration (typically an allowance parameter).
Lower numeric value = served first.

Pass instances to :class:`~simulatte.router.Router` as the
``priority_policies`` argument, e.g. ``Router(priority_policies=Pst(allowance=2.0))``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.job import BaseJob
    from simulatte.server import Server


class Pst:
    """Planned Slack Time at *server* with per-operation allowance.

    Defined as

    ``pst_ij = (d_i - now) - sum(p_ik + k for k in R_i_from_j)``

    where ``R_i_from_j`` is the set of operations from *server* through
    the end of the routing and ``k`` is the per-operation queue-time
    allowance. Lower PST = more urgent (the job is closer to being late).

    Returns ``inf`` if *server* is not in the job's routing or has
    already been exited — matching the convention used by
    :meth:`simulatte.policies.slar.Slar.pst_priority_policy`.

    Args:
        allowance: Per-operation queue-time allowance ``k`` (>= 0).
            Defaults to ``0.0``.

    Raises:
        ValueError: If ``allowance`` is negative.

    Reference: Land & Gaalman (1998), The performance of workload
    control concepts in job shops: Improving the release method, IJPE
    56-57, 347-364. https://doi.org/10.1016/S0925-5273(98)00052-8
    """

    def __init__(self, allowance: float = 0.0) -> None:
        if allowance < 0:
            raise ValueError(f"allowance must be >= 0, got {allowance}")
        self.allowance = allowance

    def __call__(self, job: BaseJob, server: Server) -> float:
        pst = job.planned_slack_time_at(server, allowance=self.allowance)
        if pst is None:
            return float("inf")
        return pst


class Sopn:
    """Slack per Operation: :class:`Pst` divided by the remaining-op count.

    Defined as ``sopn_ij = pst_ij(k) / |R_i|`` where ``|R_i|`` is the
    count of operations not yet completed (servers not yet exited)
    including the current one. Lower SOPN = more urgent.

    Returns ``inf`` if *server* is not in the job's routing or has
    already been exited.

    Args:
        allowance: Per-operation queue-time allowance ``k`` (>= 0),
            forwarded to the underlying :class:`Pst` computation.
            Defaults to ``0.0``.

    Raises:
        ValueError: If ``allowance`` is negative.

    Reference: Kanet (1982), Note—On Anomalies in Dynamic Ratio Type
    Scheduling Rules: A Clarifying Analysis, *Management Science*,
    28(11), 1337-1341. https://doi.org/10.1287/mnsc.28.11.1337
    """

    def __init__(self, allowance: float = 0.0) -> None:
        if allowance < 0:
            raise ValueError(f"allowance must be >= 0, got {allowance}")
        self.allowance = allowance

    def __call__(self, job: BaseJob, server: Server) -> float:
        pst = job.planned_slack_time_at(server, allowance=self.allowance)
        if pst is None:
            return float("inf")
        r_i = sum(1 for s in job.servers if job.servers_exit_at[s] is None)
        return pst / r_i
