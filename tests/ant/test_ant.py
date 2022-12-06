from __future__ import annotations

import pytest

import simulatte.system
from simulatte.ant import Ant
from simulatte.ant.ant import AntStatus
from simulatte.distance import Distance
from simulatte.environment import Environment
from simulatte.location import Location
from simulatte.unitload import CaseContainer


@pytest.fixture(scope="function")
def ant(env: Environment) -> Ant:
    return Ant(env, load_timeout=3, unload_timeout=3)


class DistanceMock(Distance):
    @property
    def as_time(self) -> float:
        return 10


class SystemMock(simulatte.system.System):
    def distance(self, from_: Location, to: Location) -> DistanceMock:
        return DistanceMock(system=self, from_=from_, to=to)


def test_load(env: Environment, ant: Ant):
    def test():
        assert ant.unit_load is None

        unit_load = CaseContainer()
        yield ant.load(unit_load=unit_load)
        assert ant.unit_load is unit_load

    env.process(test())
    env.run()


def test_double_load(env: Environment, ant: Ant):
    def test():
        unit_load = CaseContainer()
        yield ant.load(unit_load=unit_load)

        with pytest.raises(RuntimeError):
            yield ant.load(unit_load=unit_load)

    env.process(test())
    env.run()


def test_release_free(env: Environment, ant: Ant):
    def test():
        with pytest.raises(ValueError):
            yield ant.release_current()

    env.process(test())
    env.run()


def test_timings_and_status(env: Environment, ant: Ant):
    def test():
        system = SystemMock(env)
        assert ant.status == AntStatus.IDLE

        yield env.timeout(100)

        ant_request = ant.request()
        yield ant_request

        assert ant.status == AntStatus.IDLE
        mission_start_time = env.now
        ant.mission_started()
        assert ant.status == AntStatus.WAITING_UNLOADED
        assert ant._mission_history == [mission_start_time]

        # Mock the ant moving to a location
        destination = Location(name="store")
        ant_move_proc = ant.move_to(system=system, location=destination)
        # Check the status during the ant travel
        yield env.timeout(5)
        assert ant.status == AntStatus.TRAVELING_UNLOADED
        # Wait for the ant to arrive
        yield ant_move_proc
        # Check the status after the ant arrived
        assert ant.status == AntStatus.WAITING_UNLOADED
        # Check the travel time and current location
        assert ant.current_location == destination
        assert ant._travel_time == 10

        # Mock the ant waiting to be loaded at a store
        ant.waiting_to_be_loaded()
        start = env.now
        # Check that the timestamp of the start of the waiting time is correct
        assert ant.loading_waiting_time_start == start
        # Mimic the ant waiting for 5 seconds at the store
        yield env.timeout(5)
        # Wait for the ant to be loaded
        yield ant.load(unit_load=CaseContainer())
        # Check the status after the ant was loaded
        assert env.now == start + 5 + ant.load_timeout
        assert ant.loading_waiting_times == [5 + ant.load_timeout]
        assert ant.status == AntStatus.WAITING_LOADED

        # Mock the ant moving to a PickingCell
        destination = Location(name="cell")
        ant_move_proc = ant.move_to(system=system, location=destination)
        # Check the status during the ant travel
        yield env.timeout(5)
        assert ant.status == AntStatus.TRAVELING_LOADED
        # Wait for the ant to arrive
        yield ant_move_proc
        # Check the status after the ant arrived
        assert ant.status == AntStatus.WAITING_LOADED
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
        ant_move_proc = ant.move_to(system=system, location=destination)
        # Check the status during the ant travel
        yield env.timeout(5)
        assert ant.status == AntStatus.TRAVELING_LOADED
        # Wait for the ant to arrive
        yield ant_move_proc
        # Check the status after the ant arrived
        assert ant.status == AntStatus.WAITING_LOADED
        # Check the travel time and current location
        assert ant.current_location == destination
        assert ant._travel_time == 30

        yield ant.unload()
        assert ant.status == AntStatus.WAITING_LOADED

        ant.mission_ended()
        mission_end_time = env.now
        assert ant.status == AntStatus.IDLE
        assert ant._mission_history == [mission_start_time, mission_end_time]

        assert ant.mission_time == 58
        assert ant.saturation == 58 / 158
        assert ant.idle_time == 100
        assert ant._travel_time == 30
        assert ant.waiting_time == 58 - 30

        # assert (
        #     sum(ant.loading_waiting_times)
        #     + sum(ant.feeding_area_waiting_times)
        #     + sum(ant.staging_area_waiting_times)
        #     + sum(ant.unloading_waiting_times)
        #     + sum(ant.picking_waiting_times)
        #     == 58 - 30
        # )

    env.process(test())
    env.run()
