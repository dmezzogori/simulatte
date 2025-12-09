from __future__ import annotations

import enum
from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import simpy
from simpy.resources.resource import Release, Request

from simulatte.utils import EnvMixin

if TYPE_CHECKING:
    from simulatte.typings import ProcessGenerator


class ArmPosition(enum.Enum):
    AT_PICKUP = 0
    AT_RELEASE = 1


class Robot(simpy.Resource, EnvMixin):
    def __init__(self, *, pick_timeout: float, place_timeout: float, rotation_timeout: float) -> None:
        EnvMixin.__init__(self)
        simpy.Resource.__init__(self, env=self.env, capacity=1)

        self.pick_timeout = pick_timeout
        self.place_timeout = place_timeout
        self.rotation_timeout = rotation_timeout
        self.arm_position = ArmPosition.AT_PICKUP
        self._worked_time = 0
        self._movements = 0
        self._saturation_history: list[tuple[float, float]] = [(0, 0)]
        self._productivity_history: list[tuple[float, float]] = [(0, 0)]
        self._history: list[tuple[float, float]] = []
        self._request_timestamps = {}

    def request(self, *args, **kwargs) -> Request:
        def request_callback(request):
            self._request_timestamps[request] = self.env.now

        req = super().request(*args, **kwargs)
        req.callbacks.append(request_callback)
        return req

    def release(self, request, *args, **kwargs) -> Release:
        release_event = super().release(request, *args, **kwargs)
        self._history.append((self._request_timestamps[request], self.env.now))
        return release_event

    @property
    def productivity(self) -> float:
        return self._movements / self.env.now

    @property
    def worked_time(self) -> float:
        return self._worked_time

    @worked_time.setter
    def worked_time(self, value: float) -> None:
        self._worked_time = value
        self._saturation_history.append((self.env.now, self.saturation))

    @property
    def saturation(self) -> float:
        return self._worked_time / self.env.now

    @property
    def idle_time(self) -> float:
        return self.env.now - self.worked_time - self._history[0][0]

    def _pick_process(self) -> ProcessGenerator:
        if self.arm_position == ArmPosition.AT_RELEASE:
            yield self.rotate()

        yield self.env.timeout(self.pick_timeout)
        self.worked_time += self.pick_timeout
        self.arm_position = ArmPosition.AT_PICKUP

    def pick(self) -> simpy.Process:
        return self.env.process(self._pick_process())

    def _place_process(self) -> ProcessGenerator:
        if self.arm_position == ArmPosition.AT_PICKUP:
            yield self.rotate()

        yield self.env.timeout(self.place_timeout)
        self.worked_time += self.place_timeout
        self.arm_position = ArmPosition.AT_RELEASE
        self._movements += 1
        self._productivity_history.append((self.env.now, self.productivity))

    def place(self) -> simpy.Process:
        return self.env.process(self._place_process())

    def _rotate_process(self) -> ProcessGenerator:
        yield self.env.timeout(self.rotation_timeout)
        self.worked_time += self.rotation_timeout

    def rotate(self) -> simpy.Process:
        return self.env.process(self._rotate_process())

    def plot(self, *, show_productivity=False) -> None:
        x = [t / 60 / 60 for t, _ in self._saturation_history]
        y = [s * 100 for _, s in self._saturation_history]
        plt.plot(x, y)
        plt.xlabel("Time [h]")
        plt.ylabel("Saturation [%]")
        plt.title("Robot Productivity")
        plt.ylim([0, 100])
        plt.show()

        if show_productivity:
            x = [t for t, _ in self._productivity_history]
            y = [p * 60 * 60 for _, p in self._productivity_history]
            plt.plot(x, y)
            plt.title("Productivity [pcs/h]")
            plt.show()
