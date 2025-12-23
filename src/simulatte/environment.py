from __future__ import annotations

import simpy


class Environment(simpy.Environment):
    """
    Thin wrapper around ``simpy.Environment``.

    Historically this was a global singleton; we now allow callers to
    instantiate environments explicitly so simulations can coexist in the
    same process.
    """

    def step(self) -> None:
        """
        Process the next event in the queue.

        If user interrupts the simulation via KeyboardInterrupt
        raise a StopSimulation exception to gently pause the simulation.
        """

        try:
            super().step()
        except KeyboardInterrupt:  # pragma: no cover
            raise simpy.core.StopSimulation("KeyboardInterrupt")
