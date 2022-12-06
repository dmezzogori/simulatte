import simpy

import simulatte
from simulatte.location import Location


class ServicePoint(simpy.PriorityResource):
    """
    An instance of this class represents a ServicePoint: a position
    where ants go to be served.
    """

    def __init__(self, *, location: Location, capacity=1):
        """
        Initialise.

        :param env: The simulation environment
        :param loc: The node where the service point is placed.
        """
        self.env = simulatte.Environment()
        self.location = location
        super().__init__(self.env, capacity=capacity)
