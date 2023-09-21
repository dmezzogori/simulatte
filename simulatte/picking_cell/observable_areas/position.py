from __future__ import annotations

from simpy.core import BoundClass
from simpy.resources.resource import Release, Request, Resource
from simulatte.environment import Environment


class OccupationRequest(Request):
    """
    An instance of this request is a request made to a position
    with an associated FeedingOperation.

    It is instantiated when an agv wants to occupy a Position.
    """

    def __init__(self, resource, operation):
        """
        Initialize.

        :param agv: The agv that wants to occupy the resource
        :param priority: The priority of the request
        """
        self.operation = operation
        super().__init__(resource)


class Position(Resource):
    """
    An instance of this class represents a position that can be booked and occupied by an agv inside the picking cell.
    """

    # Method to require the position
    request = BoundClass(OccupationRequest)

    # Method to release the position
    release = BoundClass(Release)

    def __init__(self, name: str, *args, **kwargs):
        super().__init__(*args, **kwargs, env=Environment())
        self.name = name

    def __repr__(self) -> str:
        return self.name

    @property
    def busy(self):
        return len(self.users) > 0

    @property
    def empty(self):
        return len(self.users) == 0

    def release_current(self):
        if len(self.users) == 0:
            raise Exception("Position cannot release unexisting request.")
        self.release(self.users[0])

    @property
    def id(self):
        if self.operation:
            return self.operation.layers[0].id

    @property
    def operation(self):
        if len(self.users) == 0:
            return None
        return self.users[0].operation

    @property
    def product(self):
        if self.operation:
            return self.operation.product

    @property
    def ant(self):
        if len(self.users) == 0:
            return None
        return self.users[0].operation.agv
