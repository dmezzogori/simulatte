from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.protocols import HasEnv
from simulatte.unitload import CaseContainer
from simulatte.utils import EnvMixin

if TYPE_CHECKING:
    from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class Operation(EnvMixin, HasEnv):
    def __init__(
        self,
        *,
        unit_load: CaseContainer,
        location: WarehouseLocation,
        env: Environment,
        priority: int = 0,
    ) -> None:
        EnvMixin.__init__(self, env=env)

        self.unit_load = unit_load
        self.location = location
        self.priority = priority

    @property
    def position(self) -> int:
        return self.location.x

    @property
    def floor(self) -> int:
        return self.location.y

    def __eq__(self, other) -> bool:
        return self.unit_load == other.unit_load


class InputOperation(Operation):
    """Warehouse input operation"""

    def __init__(
        self,
        *,
        unit_load: CaseContainer,
        location: WarehouseLocation,
        priority: int,
        env: Environment,
    ) -> None:
        super().__init__(unit_load=unit_load, location=location, priority=priority, env=env)
        self.lift_process = None
        self.lifted = self.env.event()


class OutputOperation(Operation):
    """Warehouse output operation"""

    pass
