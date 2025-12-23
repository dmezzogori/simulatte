"""AGVServer - Server specialization for AGV transport operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.server import Server

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


class AGVServer(Server):
    """An AGV (Automated Guided Vehicle) server for transport operations.

    AGVServer extends Server to model transport resources. Each AGV has capacity=1
    (can only transport one job at a time) and tracks travel time metrics.

    Attributes:
        travel_time_fn: Callable(origin, destination) -> travel time.
        agv_id: Optional identifier for this AGV.
        trip_count: Number of completed trips.
        total_travel_time: Cumulative travel time.
    """

    def __init__(
        self,
        *,
        env: Environment,
        travel_time_fn: Callable[[Server, Server], float],
        shopfloor: ShopFloor | None = None,
        agv_id: str | None = None,
    ) -> None:
        """Initialize an AGVServer.

        Args:
            env: The simulation environment.
            travel_time_fn: Callable(origin, destination) returning travel time.
            shopfloor: Optional shopfloor to register with.
            agv_id: Optional identifier for this AGV.
        """
        super().__init__(env=env, capacity=1, shopfloor=shopfloor)

        self.travel_time_fn = travel_time_fn
        self.agv_id = agv_id or f"agv-{self._idx}"

        # Metrics
        self.trip_count: int = 0
        self.total_travel_time: float = 0
        self._current_location: Server | None = None

    def __repr__(self) -> str:
        return f"AGVServer(id={self.agv_id})"

    @property
    def current_location(self) -> Server | None:
        """The AGV's current location (last destination)."""
        return self._current_location

    @property
    def average_travel_time(self) -> float:
        """Average time per trip."""
        if self.trip_count == 0:
            return 0.0
        return self.total_travel_time / self.trip_count

    def travel(self, origin: Server, destination: Server) -> ProcessGenerator:
        """Execute a travel operation from origin to destination.

        Args:
            origin: Starting location.
            destination: Target location.

        Yields:
            SimPy timeout for travel duration.
        """
        travel_time = self.travel_time_fn(origin, destination)
        yield self.env.timeout(travel_time)

        self.trip_count += 1
        self.total_travel_time += travel_time
        self.worked_time += travel_time
        self._current_location = destination

    def travel_to(self, destination: Server) -> ProcessGenerator:
        """Travel from current location to destination.

        Args:
            destination: Target location.

        Yields:
            SimPy timeout for travel duration.

        Raises:
            ValueError: If current location is not set.
        """
        if self._current_location is None:
            raise ValueError("AGV has no current location. Use travel() first.")
        yield from self.travel(self._current_location, destination)

    def set_location(self, location: Server) -> None:
        """Set the AGV's initial or current location without travel.

        Args:
            location: The location to set.
        """
        self._current_location = location
