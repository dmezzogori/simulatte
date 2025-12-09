from __future__ import annotations

import matplotlib.pyplot as plt

from simulatte.robot import ArmPosition, Robot


def test_robot_pick_place_and_metrics(monkeypatch):
    # Avoid opening plot windows
    monkeypatch.setattr(plt, "show", lambda *_, **__: None)

    robot = Robot(pick_timeout=1, place_timeout=2, rotation_timeout=1)
    env = robot.env

    def work_cycle():
        with robot.request() as req:
            yield req
            yield robot.pick()
            yield robot.place()

    env.process(work_cycle())
    env.run()

    assert robot.arm_position is ArmPosition.AT_RELEASE
    assert robot.worked_time == robot.pick_timeout + robot.rotation_timeout + robot.place_timeout
    assert robot._movements == 1
    assert robot.productivity == robot._movements / env.now
    assert robot.saturation == robot.worked_time / env.now
    assert robot._history  # request / release timestamps recorded

    # Calling plot should not raise after monkeypatching show
    robot.plot(show_productivity=False)


def test_robot_rotate_updates_worked_time():
    robot = Robot(pick_timeout=1, place_timeout=1, rotation_timeout=2)
    env = robot.env

    robot.rotate()
    env.run()

    assert robot.worked_time == 2
    assert isinstance(robot._saturation_history[-1], tuple)
    assert robot.arm_position is ArmPosition.AT_PICKUP

    def quick_request():
        with robot.request() as req:
            yield req
            yield env.timeout(0)

    env.process(quick_request())
    env.run()

    assert robot._history  # release recorded


def test_robot_idle_time_and_plot_productivity(monkeypatch):
    monkeypatch.setattr(plt, "show", lambda *_, **__: None)
    robot = Robot(pick_timeout=1, place_timeout=1, rotation_timeout=1)
    env = robot.env

    def cycle():
        with robot.request() as req:
            yield req
            robot.arm_position = ArmPosition.AT_RELEASE
            yield robot.pick()  # triggers rotate branch
            yield robot.place()

    env.process(cycle())
    env.run()

    assert robot.idle_time >= 0
    robot.plot(show_productivity=True)
