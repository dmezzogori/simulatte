from __future__ import annotations

from simulatte.policies.agv_selection_policy.base import MultiAGVSelectionPolicy


class ReverseFeedingSelectionPolicy(MultiAGVSelectionPolicy):
    def __call__(self, *, agvs, exceptions=None):
        """
        Returns the agvs in reverse order.
        To be used to sort the idle feeding agvs from the last to the first.
        """

        if exceptions is not None:
            exceptions = set(exceptions)
            agvs = (agv for agv in agvs if agv not in exceptions)

        return tuple(reversed(tuple(agvs)))  # reverse the list of agvs
