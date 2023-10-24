from __future__ import annotations

import abc
import random
import time
from collections.abc import Iterable
from typing import Generic, TypeVar

import numpy as np
from simpy import Event
from simulatte.environment import Environment
from simulatte.logger import logger

SimulationConfig = TypeVar("SimulationConfig")


class Simulation(abc.ABC, Generic[SimulationConfig]):
    """
    Base class to build and run a simulation.

    Attributes:
        config: The configuration of the simulation.
        env: The simulatte environment of the simulation.
    """

    def __init__(self, *, config: SimulationConfig, seed: int | None = None) -> None:
        """
        Initialize the simulation.
        Resets the Identifiable and Singleton metaclasses.
        Sets the seed for random number generation.

        Args:
            config: The configuration of the simulation.
            seed: The seed to use for random number generation.

        Returns:
            None
        """
        from simulatte.utils import Identifiable, Singleton

        Identifiable.reset()
        Singleton.clear()

        self.config = config
        self.seed = seed
        self.env = Environment()

        if self.seed is not None:
            random.seed(self.seed)
            np.random.seed(self.seed)

        self.build()

    def run(self, *, until: int | float | Event | None = None, debug=False) -> None:
        """
        Run the simulation.
        Prints the time it took to run the simulation.

        Args:
            until: The time or event until the simulation should run.
            debug: Whether to print debug messages.

        Returns:
            None
        """

        start = time.time()

        if not debug:
            logger.remove()

        self.env.run(until=until)

        end = time.time()

        print(f"Simulation took: {(end - start):.2f} seconds")

    @abc.abstractmethod
    def build(self) -> None:
        """
        Builds all components needed for the simulation.

        Returns:
            None
        """

        ...

    @property
    @abc.abstractmethod
    def results(self) -> Iterable[int | float]:
        """
        Returns the result(s) of the simulation.
        Useful to collect result(s) from multiple simulations.

        Returns:
            An iterable containing the result(s) of the simulation.
        """

        ...

    @abc.abstractmethod
    def summary(self) -> None:
        """
        Prints a summary of the simulation, delegating to the components.

        Returns:
            None
        """

        ...
