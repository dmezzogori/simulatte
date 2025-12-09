from __future__ import annotations

from typing import cast

import pytest

from simulatte.agv import AGVKind
from simulatte.agv.agv import AGV
from simulatte.agv.agv_mission import AGVMission
from simulatte.agv.agv_status import AGVStatus
from simulatte.agv.agv_trip import AGVTrip
from simulatte.location import Location
from simulatte.unitload.case_container import CaseContainer


class FakeTrip(AGVTrip):
    @property
    def distance(self) -> float:
        return 10.0

    def define_agv_status(self):
        return AGVStatus.TRAVELING_UNLOADED, AGVStatus.IDLE


class FakeAGV(AGV):
    def _init_trip(self, destination) -> AGVTrip:
        return FakeTrip(agv=self, destination=destination)


def make_agv() -> FakeAGV:
    return FakeAGV(kind=AGVKind.FEEDING, load_timeout=1, unload_timeout=1, speed=1.0)


def test_agv_request_and_release_sets_mission_times():
    agv = make_agv()

    def user():
        with agv.request(operation="op") as req:
            yield req
            assert isinstance(agv.current_mission, AGVMission)
            agv.release(req)

    agv.env.process(user())
    agv.env.run()

    assert len(agv.missions) == 1
    mission = agv.missions[0]
    assert mission.start_time == 0
    assert mission.end_time == agv.env.now
    assert agv.status == AGVStatus.IDLE
    assert agv.total_mission_duration == pytest.approx(mission.duration)


def test_load_and_unload_require_proper_status_and_unit_load():
    agv = make_agv()
    agv.status = AGVStatus.WAITING_TO_BE_LOADED

    loader = agv.load(unit_load=cast(CaseContainer, "payload"))
    agv.env.run()
    assert agv.unit_load == "payload"
    assert loader.value is None

    agv.unit_load = None
    with pytest.raises(ValueError):
        agv.unload()
        agv.env.run()

    agv.unit_load = cast(CaseContainer, "payload")
    agv.status = AGVStatus.WAITING_TO_BE_UNLOADED
    unloader = agv.unload()
    agv.env.run()
    assert agv.unit_load is None
    assert unloader.value is None


def test_move_to_updates_trip_history_and_metrics():
    agv = make_agv()
    start = Location()
    dest = Location()
    agv.current_location = start

    proc = agv.move_to(location=dest)
    agv.env.run()

    assert proc.value is None
    assert agv.current_location is dest
    assert agv._travel_time == pytest.approx(10.0)
    assert agv.trips and isinstance(agv.trips[0], FakeTrip)
    assert agv.waiting_time == agv.total_mission_duration - agv._travel_time
