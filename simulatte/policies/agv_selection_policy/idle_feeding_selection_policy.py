from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.policies.agv_selection_policy.base import MultiAGVSelectionPolicy

if TYPE_CHECKING:
    from simulatte.agv.agv import AGV


class IdleFeedingSelectionPolicy(MultiAGVSelectionPolicy):
    @staticmethod
    def sorter(agv: AGV):
        """
        Return a tuple of the number of users and the number of processes in queue for the AGV.
        Used to sort the AGVs from the least busy to the most busy.
        """

        timestamp = 0
        if agv.current_mission is not None:
            timestamp = agv.current_mission.start_time or agv.current_mission.request.time

        return agv.n_users, agv.n_queue, timestamp

    def __call__(self, *, agvs, exceptions=None):
        """
        Selection policy for feeding operations.
        """

        if exceptions is not None:
            agvs = set(agvs) - set(exceptions)

        return sorted(agvs, key=self.sorter)
