import simpy


class ServicePoint(simpy.PriorityResource):
    """
    An instance of this class represents a ServicePoint: a position
    where ants go to be served.
    """

    def __init__(self, env, loc, capacity=1):
        """
        Initialise.

        :param env: The simulation environment
        :param loc: The node where the service point is placed.
        """
        self.env = env
        self.loc = loc
        super(ServicePoint, self).__init__(env, capacity=capacity)
