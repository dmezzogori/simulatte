from __future__ import annotations

from simpy import Environment as SimpyEnvironment
from simulatte.utils.singleton import Singleton


class Environment(SimpyEnvironment, metaclass=Singleton):
    """
    Singleton class for the simulation environment.
    """

    pass
