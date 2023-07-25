from __future__ import annotations


class Location:
    def __init__(self, element=None):
        self.element = element

    def __str__(self) -> str:
        return self.name

    @property
    def name(self):
        return f"{self.__class__.__name__}_{self.element.name}"


class InputLocation(Location):
    """
    Input location is a location where the AGV can pick up a unit load.
    """

    pass


class OutputLocation(Location):
    """
    Output location is a location where the AGV can drop off a unit load.
    """

    pass


class StagingLocation(Location):
    """
    Staging location is a location inside a picking cell where an AGV waits.
    """

    pass


class InternalLocation(Location):
    """
    Internal location is a location inside a picking cell where an AGV can
    be picked from.
    """

    pass


class AGVRechargeLocation(Location):
    """
    AGV recharge location is a location where an AGV can recharge its battery.
    """

    pass
