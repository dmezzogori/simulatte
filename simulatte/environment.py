from __future__ import annotations

from simpy import Environment as SimpyEnvironment
from simpy.core import StopSimulation
from simulatte.utils.singleton import Singleton


class Environment(SimpyEnvironment, metaclass=Singleton):
    """
    Singleton class for the simulation environment.
    """

    def step(self):
        try:
            super().step()
        except KeyboardInterrupt:
            raise StopSimulation("KeyboardInterrupt")
