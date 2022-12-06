from simulatte.stores.warehouse_location import WarehouseLocation
from simulatte.unitload import Pallet


class Operation:
    """
    An instance of this class represents an operation the
    AVS/RS should take care of.
    """

    def __init__(self, env, unit_load: Pallet, location: WarehouseLocation, priority):
        """
        Inittialise.

        :param env: The simulation environment.
        :param unit_load: The unitload moved.
        :param location: The location where the unitload will be stored or taken.
        :param buffer: The buffer where the interchange between lift and shuttle is made.
        :param priority: The priority of the operation.
        """
        self.unitload = unit_load
        self.location = location
        self.priority = priority

    @property
    def position(self):
        return self.location.x

    @property
    def floor(self):
        return self.location.y

    def __eq__(self, other):
        return self.unitload == other.unitload


class InputOperation(Operation):
    """An instance of this class represents an input operation"""

    def __init__(self, env, unit_load, location, priority):
        """
        Initialise.

        :attr lift_process: The process through which the unitload is moved by the input lift.
        :attr lifted: An event triggered when the operation can be taken by the shuttle.
        """
        super().__init__(env, unit_load, location, priority)
        self.lift_process = None
        self.lifted_flag = False
        self.lifted = env.event()


class OutputOperation(Operation):
    """An instance of this class represents an output operation."""

    pass
