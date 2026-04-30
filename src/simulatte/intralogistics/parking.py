from __future__ import annotations

from typing import TYPE_CHECKING

import simpy
from simpy.events import ProcessGenerator

if TYPE_CHECKING:
    from simulatte.environment import Environment
    from simulatte.intralogistics.agv import AGV
    from simulatte.intralogistics.graph import Node


class ParkingArea:
    """A facility where idle AGVs wait for their next assignment.

    Wraps a ``simpy.Resource`` to model finite parking capacity. Each AGV's
    resource request is tracked individually so that ``leave()`` releases the
    correct slot.
    """

    def __init__(
        self,
        *,
        env: Environment,
        name: str,
        node: Node,
        capacity: int,
    ) -> None:
        self.env = env
        self.name = name
        self.node = node
        self._resource = simpy.Resource(env, capacity=capacity)
        self._agv_requests: dict[AGV, simpy.resources.resource.Request] = {}

    def enter(self, agv: AGV) -> ProcessGenerator:
        """Request a parking slot. Blocks if the area is full."""
        self.env.debug(
            f"[{self.name}] {agv.agv_id} entering",
            component="ParkingArea",
        )
        req = self._resource.request()
        yield req
        self._agv_requests[agv] = req
        self.env.debug(
            f"[{self.name}] {agv.agv_id} parked",
            component="ParkingArea",
        )

    def leave(self, agv: AGV) -> None:
        """Release the parking slot held by *agv*.

        Raises ``KeyError`` if the AGV is not currently parked.
        """
        req = self._agv_requests.pop(agv)
        self._resource.release(req)
        self.env.debug(
            f"[{self.name}] {agv.agv_id} left",
            component="ParkingArea",
        )

    def __repr__(self) -> str:
        return (
            f"ParkingArea(name={self.name!r}, "
            f"node={self.node.id!r}, "
            f"capacity={self._resource.capacity})"
        )
