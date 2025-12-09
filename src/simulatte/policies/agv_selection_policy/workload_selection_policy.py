from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.policies.agv_selection_policy.base import AGVSelectionPolicy

if TYPE_CHECKING:
    from simulatte.agv.agv import AGV


class WorkloadAGVSelectionPolicy(AGVSelectionPolicy):
    """
    Select the AGV with the least workload.
    The workload is defined as the number of users and the number of missions in the queue.

    If there are multiple AGVs with the same workload, the AGV with the earliest start time is selected.
    """

    @staticmethod
    def sorter(agv: AGV):
        """
        Return a tuple of the number of users and the number of processes in queue for the AGV.
        Used to sort the AGVs from the least busy to the most busy.
        """

        return agv.n_users, agv.n_queue

    def __call__(self, *, agvs, exceptions=None) -> AGV:
        """
        Select the AGV with the least workload.
        Optionally, exclude some AGVs from the selection.
        """

        if exceptions is not None:
            agvs = set(agvs) - set(exceptions)

        return min(agvs, key=self.sorter)
