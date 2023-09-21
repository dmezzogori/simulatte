from __future__ import annotations

import pytest
import simulatte.controllers
from simulatte.agv import AGV, AGVStatus
from simulatte.distance import Distance
from simulatte.environment import Environment
from simulatte.location import Location
from simulatte.unitload import CaseContainer


@pytest.fixture(scope="function")
def ant(env: Environment) -> AGV:
    return AGV(env, load_timeout=3, unload_timeout=3)


class DistanceMock(Distance):
    @property
    def as_time(self) -> float:
        return 10


class SystemMock(simulatte.system.SystemController):
    def distance(self, from_: Location, to: Location) -> DistanceMock:
        return DistanceMock(system=self, from_=from_, to=to)


def test_load(env: Environment, ant: AGV):
    def test():
        assert ant.unit_load is None

        unit_load = CaseContainer()
        yield ant.load(unit_load=unit_load)
        assert ant.unit_load is unit_load

    env.process(test())
    env.run()


def test_double_load(env: Environment, ant: AGV):
    def test():
        unit_load = CaseContainer()
        yield ant.load(unit_load=unit_load)

        with pytest.raises(RuntimeError):
            yield ant.load(unit_load=unit_load)

    env.process(test())
    env.run()


def test_release_free(env: Environment, ant: AGV):
    def test():
        with pytest.raises(ValueError):
            yield ant.release_current()

    env.process(test())
    env.run()


def test_timings_and_status(env: Environment, ant: AGV):
    def test():
        SystemMock(env)
        assert ant.status == AGVStatus.IDLE

        yield env.timeout(100)

        ant_request = ant.request()
        yield ant_request

        assert ant.status == AGVStatus.IDLE
        mission_start_time = env.now
        ant.mission_started()
        assert ant.status == AGVStatus.WAITING_TO_BE_LOADED
        assert ant._mission_history == [mission_start_time]

        # Mock the agv moving to a location
        destination = Location(name="store")
        ant_move_proc = ant.move_to(location=destination)
        # Check the status during the agv travel
        yield env.timeout(5)
        assert ant.status == AGVStatus.TRAVELING_UNLOADED
        # Wait for the agv to arrive
        yield ant_move_proc
        # Check the status after the agv arrived
        assert ant.status == AGVStatus.WAITING_TO_BE_LOADED
        # Check the travel time and current location
        assert ant.current_location == destination
        assert ant._travel_time == 10

        # Mock the agv waiting to be loaded at a store
        ant.waiting_to_be_loaded()
        start = env.now
        # Check that the timestamp of the start of the waiting time is correct
        assert ant._loading_waiting_time_start == start
        # Mimic the agv waiting for 5 seconds at the store
        yield env.timeout(5)
        # Wait for the agv to be loaded
        yield ant.load(unit_load=CaseContainer())
        # Check the status after the agv was loaded
        assert env.now == start + 5 + ant.load_timeout
        assert ant._loading_waiting_times == [5 + ant.load_timeout]
        assert ant.status == AGVStatus.WAITING_TO_BE_UNLOADED

        # Mock the agv moving to a PickingCell
        destination = Location(name="cell")
        ant_move_proc = ant.move_to(location=destination)
        # Check the status during the agv travel
        yield env.timeout(5)
        assert ant.status == AGVStatus.TRAVELING_LOADED
        # Wait for the agv to arrive
        yield ant_move_proc
        # Check the status after the agv arrived
        assert ant.status == AGVStatus.WAITING_TO_BE_UNLOADED
        # Check the travel time and current location
        assert ant.current_location == destination
        assert ant._travel_time == 20

        # Waiting to enter the PickingCell
        ant.waiting_to_enter_staging_area()
        assert ant._waiting_to_enter_staging_area == env.now
        yield env.timeout(7)
        # Now inside the PickingCell StagingArea
        ant.enter_staging_area()
        assert ant.feeding_area_waiting_times == [7]
        assert ant._waiting_to_enter_internal_area == env.now
        yield env.timeout(3)
        # Now inside the PickingCell InternalArea
        ant.enter_internal_area()
        assert ant.staging_area_waiting_times == [3]
        assert ant._waiting_to_enter_internal_area is None
        assert ant._waiting_to_be_unloaded == env.now
        # Now the picking begins
        yield env.timeout(5)
        ant.picking_begins()
        assert ant.unloading_waiting_times == [5]
        assert ant._waiting_picking_to_end == env.now
        # Now the picking ends
        yield env.timeout(2)
        ant.picking_ends()
        assert ant.picking_waiting_times == [2]

        destination = Location(name="rest")
        ant_move_proc = ant.move_to(location=destination)
        # Check the status during the agv travel
        yield env.timeout(5)
        assert ant.status == AGVStatus.TRAVELING_LOADED
        # Wait for the agv to arrive
        yield ant_move_proc
        # Check the status after the agv arrived
        assert ant.status == AGVStatus.WAITING_TO_BE_UNLOADED
        # Check the travel time and current location
        assert ant.current_location == destination
        assert ant._travel_time == 30

        yield ant.unload()
        assert ant.status == AGVStatus.WAITING_TO_BE_UNLOADED

        ant.mission_ended()
        mission_end_time = env.now
        assert ant.status == AGVStatus.IDLE
        assert ant._mission_history == [mission_start_time, mission_end_time]

        assert ant.mission_time == 58
        assert ant.saturation == 58 / 158
        assert ant.idle_time == 100
        assert ant._travel_time == 30
        assert ant.waiting_time == 58 - 30

        # assert (
        #     sum(agv.loading_waiting_times)
        #     + sum(agv.feeding_area_waiting_times)
        #     + sum(agv.staging_area_waiting_times)
        #     + sum(agv.unloading_waiting_times)
        #     + sum(agv.picking_waiting_times)
        #     == 58 - 30
        # )

    env.process(test())
    env.run()
