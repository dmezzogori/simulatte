from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import simpy
import simpy.resources.resource
from simpy.events import ProcessGenerator

from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import Deltas
from simulatte.intralogistics.events import ParkingEntered, ParkingLeft

if TYPE_CHECKING:
    from simulatte.environment import Environment
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.graph import Node


class ParkingArea(Entity, kind="parking_area"):
    """A facility where idle AGVs wait for their next assignment.

    Wraps a ``simpy.Resource`` to model finite parking capacity. Each AGV's
    resource request is tracked individually so that ``leave()`` releases the
    correct slot. Its id is ``name``.

    Events (spec §6.4): ``parking.entered`` when ``enter`` obtained a slot and ``parking.left`` when ``leave``
    released it. ``FleetCoordinator`` does not call either.
    """

    state_schema: ClassVar[StateSchema] = StateSchema({"parked": FieldSpec("str", collection="list")})

    def __init__(
        self,
        *,
        env: Environment,
        name: str,
        node: Node,
        capacity: int,
        label: str | None = None,
    ) -> None:
        self.env = env
        self.name = name
        self.node = node
        self._resource = simpy.Resource(env, capacity=capacity)
        self._agv_requests: dict[AGV, simpy.resources.resource.Request] = {}
        env.entities.attach(self, name=name, label=label)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: the ids of the parked AGVs, in order of arrival."""
        return {"parked": [agv.id for agv in self._agv_requests], "label": self.label}

    @property
    def available_capacity(self) -> int:
        return int(self._resource.capacity - self._resource.count)

    def enter(self, agv: AGV) -> ProcessGenerator:
        """Request a parking slot. Blocks if the area is full.

        Emits ``parking.entered`` once the slot is obtained. An AGV that enters again without leaving keeps its
        place in ``parked`` (its new slot replaces the tracked one), so that event carries no delta.
        """
        req = self._resource.request()
        yield req
        requests = self._agv_requests
        new = agv not in requests
        requests[agv] = req
        env = self.env
        if env.wants(ParkingEntered):
            build = Deltas.build()
            if new:
                build.insert(self.id, "parked", len(requests) - 1, agv.id)
            env.emit(ParkingEntered(area=self.id, agv=agv.id, deltas=build.done()))

    def leave(self, agv: AGV) -> None:
        """Release the parking slot held by *agv*.

        Raises ``KeyError`` if the AGV is not currently parked.
        """
        req = self._agv_requests.pop(agv)
        self._resource.release(req)
        env = self.env
        if env.wants(ParkingLeft):
            env.emit(
                ParkingLeft(area=self.id, agv=agv.id, deltas=Deltas.build().remove(self.id, "parked", agv.id).done())
            )

    def __repr__(self) -> str:
        return f"ParkingArea(name={self.name!r}, node={self.node.id!r}, capacity={self._resource.capacity})"
