import pytest

from simulatte.picking_cell.robot import Robot
from simulatte.system import System


@pytest.fixture(scope="function")
def robot(system: System) -> Robot:
    return Robot(system=system, pick_timeout=5, place_timeout=5, rotation_timeout=10)


def test_robot_timings(system: System, robot: Robot):
    def test():
        yield robot.pick()
        yield robot.place()

        tot_duration = robot.pick_timeout + robot.rotation_timeout + robot.place_timeout
        assert system.env.now == tot_duration

        yield robot.pick()

        tot_duration += robot.rotation_timeout + robot.pick_timeout
        assert system.env.now == tot_duration

        yield robot.place()

        tot_duration += robot.rotation_timeout + robot.place_timeout

        assert system.env.now == tot_duration

        yield robot.place()

        assert system.env.now == tot_duration + robot.place_timeout

    system.env.process(test())
    system.env.run()
