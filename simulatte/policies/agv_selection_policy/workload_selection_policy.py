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
        Return a tuple of the number of users, the number of missions in the queue
        and the start time of the current mission.
        """

        return agv.n_users, agv.n_queue  # , agv.current_mission.start_time

    def __call__(self, *, agvs, exceptions=None) -> AGV:
        """
        Select the AGV with the least workload.
        Optionally, exclude some AGVs from the selection.
        """

        if exceptions is not None:
            agvs = set(agvs) - set(exceptions)

        return min(agvs, key=self.sorter)
