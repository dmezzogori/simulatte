"""Common dispatching rules from the production-planning literature.

This package hosts dispatching rules — pure ``(job, server) -> float``
callables for queue ordering. Pass them as the ``priority_policies``
argument to ``Router``, or as the ``priority_policy`` argument to
``ProductionJob``. Lower numeric value = served first.

Rules are grouped by scheduling family:

- ``processing`` — processing-time and baseline rules.
- ``due_date`` — due-date-based rules.
- ``slack`` — slack- and ratio-based rules, including the parameterized factories.
- ``focus`` — the FOCUS self-establishing weighted-mechanism rule.
"""

from __future__ import annotations

from .due_date import earliest_due_date, modified_operational_due_date, operational_due_date
from .focus import Focus, FocusContext, FocusPriorityRule
from .processing import first_come_first_served, shortest_processing_time
from .slack import critical_ratio, planned_slack_time, slack_per_remaining_operation
from .tardiness_cost import apparent_tardiness_cost
from .work_content import work_in_next_queue

__all__ = [
    "Focus",
    "FocusContext",
    "FocusPriorityRule",
    "apparent_tardiness_cost",
    "critical_ratio",
    "earliest_due_date",
    "first_come_first_served",
    "modified_operational_due_date",
    "operational_due_date",
    "planned_slack_time",
    "shortest_processing_time",
    "slack_per_remaining_operation",
    "work_in_next_queue",
]
