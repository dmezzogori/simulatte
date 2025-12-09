from __future__ import annotations

import simpy

from simulatte.utils.singleton import Singleton


class Environment(simpy.Environment, metaclass=Singleton):
    """
    Singleton class for the simulation environment.
    """

    def step(self) -> None:
        """
        Process the next event in the queue.

        If user interrupts the simulation via KeyboardInterrupt
        raise a StopSimulation exception to gently pause the simulation.
        """

        try:
            super().step()
        except KeyboardInterrupt:
            raise simpy.core.StopSimulation("KeyboardInterrupt")
