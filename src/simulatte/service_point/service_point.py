from __future__ import annotations

import simpy

from simulatte.location import Location
from simulatte.utils import EnvMixin


class ServicePoint(simpy.PriorityResource, EnvMixin):
    """
    An instance of this class represents a ServicePoint: a position
    where ants go to be served.
    """

    def __init__(self, *, location: Location, capacity=1):
        EnvMixin.__init__(self)
        simpy.PriorityResource.__init__(self, self.env, capacity=capacity)

        self.location = location
