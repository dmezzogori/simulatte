"""Common dispatching rules from the production-planning literature.

This package hosts dispatching rules — pure ``(job, server) -> float``
callables for queue ordering. Pass them as the ``priority_policies``
argument to :class:`~simulatte.router.Router`, or as the ``priority_policy``
argument to :class:`~simulatte.job.ProductionJob`. Lower numeric value = served first.

- **Tier 1** — stateless functions grouped in :mod:`.basic`.
- **Tier 2** — parameterized factory functions in :mod:`.parametrized`.
"""

from __future__ import annotations

from .basic import (
    critical_ratio,
    earliest_due_date,
    first_come_first_served,
    modified_operational_due_date,
    operational_due_date,
    shortest_processing_time,
)
from .parametrized import planned_slack_time, slack_per_remaining_operation

__all__ = [
    "critical_ratio",
    "earliest_due_date",
    "first_come_first_served",
    "modified_operational_due_date",
    "operational_due_date",
    "planned_slack_time",
    "shortest_processing_time",
    "slack_per_remaining_operation",
]
