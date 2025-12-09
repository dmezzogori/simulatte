from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from simulatte.agv.agv_mission import AGVMission
from simulatte.agv import AGV
from simpy.resources.resource import PriorityRequest


def test_agv_mission_duration_none_until_end_time():
    mission = AGVMission(
        agv=cast(AGV, SimpleNamespace()),
        request=cast(PriorityRequest, SimpleNamespace()),
    )
    assert mission.duration is None

    mission.start_time = 1.0
    mission.end_time = 3.5

    assert mission.duration == 2.5
