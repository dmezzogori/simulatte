from __future__ import annotations

import enum
from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import simpy

from simulatte.typings import ProcessGenerator

if TYPE_CHECKING:
    from simulatte.system import System


class ArmPosition(enum.Enum):
    AT_PICKUP = 0
    AT_RELEASE = 1


class Robot(simpy.Resource):
    def __init__(self, *, system: System, pick_timeout: int, place_timeout: int, rotation_timeout: int) -> None:
        super().__init__(env=system.env, capacity=1)

        self.env = system.env
        self.pick_timeout = pick_timeout
        self.place_timeout = place_timeout
        self.rotation_timeout = rotation_timeout
        self.arm_position = ArmPosition.AT_PICKUP
        self._worked_time = 0
        self._movements = 0
        self._saturation_history: list[tuple[float, float]] = [(0, 0)]
        self._productivity_history: list[tuple[float, float]] = [(0, 0)]

    @property
    def worked_time(self) -> int:
        return self._worked_time

    @property
    def productivity(self) -> float:
        return self._movements / self.env.now

    @worked_time.setter
    def worked_time(self, value: int) -> None:
        self._worked_time = value
        self._saturation_history.append((self.env.now, self.saturation))

    @property
    def saturation(self) -> float:
        return self._worked_time / self.env.now

    @property
    def idle_time(self) -> int:
        return self.env.now - self.worked_time

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

    def plot(self) -> None:
        x = [t for t, _ in self._saturation_history]
        y = [s for _, s in self._saturation_history]
        plt.plot(x, y)
        plt.title("Saturation [%]")
        plt.show()

        x = [t for t, _ in self._productivity_history]
        y = [p * 60 * 60 for _, p in self._productivity_history]
        plt.plot(x, y)
        plt.title("Productivity [pcs/h]")
        plt.show()
