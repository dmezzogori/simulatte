"""Common dispatching rules from the production-planning literature.

This package hosts dispatching rules — pure ``(job, server) -> float``
callables for queue ordering. Pass them as the ``priority_policies``
argument to :class:`~simulatte.router.Router`, or as the ``priority_policy``
argument to :class:`~simulatte.job.ProductionJob`. Lower numeric value = served first.

- **Tier 1** — stateless functions grouped in :mod:`.basic`.
- **Tier 2** — parameterized callable classes in :mod:`.parametrized`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .basic import cr, edd, fcfs, modd, odd, spt
from .parametrized import Pst, Sopn

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import BaseJob
    from simulatte.server import Server


__all__ = [
    "Pst",
    "Sopn",
    "cr",
    "edd",
    "fcfs",
    "modd",
    "odd",
    "planned_slack_time",
    "spt",
]


def planned_slack_time(allowance: float = 0.0) -> Callable[[BaseJob, Server], float]:
    """Build a Planned Slack Time (PST) dispatching rule.

    Defined as

    ``pst_ij = (d_i - now) - sum(p_ik + k for k in R_i_from_j)``

    where ``R_i_from_j`` is the set of operations from *server* through
    the end of the routing and ``k`` is the per-operation queue-time
    allowance. Lower PST = more urgent (the job is closer to being late).

    Returns ``inf`` if *server* is not in the job's routing or has
    already been exited.

    Args:
        allowance: Per-operation queue-time allowance ``k`` (>= 0).
            Defaults to ``0.0``.

    Returns:
        A ``(job, server) -> float`` callable suitable for use as a
        ``priority_policies`` on `Router` or
        ``priority_policy`` on `ProductionJob`.

    Raises:
        ValueError: If ``allowance`` is negative.

    Reference:
        Land, M.J. & Gaalman, G.J.C. (1998). The performance of workload
        control concepts in job shops: Improving the release method.
        International Journal of Production Economics, 56-57, 347-364.
        https://doi.org/10.1016/S0925-5273(98)00052-8
    """
    if allowance < 0:
        msg = f"allowance must be >= 0, got {allowance}"
        raise ValueError(msg)

    def _pst(job: BaseJob, server: Server) -> float:
        value = job.planned_slack_time_at(server, allowance=allowance)
        return float("inf") if value is None else value

    return _pst
