from simpy import Environment as SimpyEnvironment

from simulatte.utils import Singleton


class Environment(SimpyEnvironment, metaclass=Singleton):
    pass
