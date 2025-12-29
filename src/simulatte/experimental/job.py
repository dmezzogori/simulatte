"""Transport and warehouse job types for material handling operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from simulatte.environment import Environment
from simulatte.job import BaseJob, JobType

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from simpy.core import SimTime

    from simulatte.server import Server


class TransportJob(BaseJob):
    """A transport job for moving materials between locations.

    Transport jobs represent AGV or other vehicle movements carrying cargo
    from an origin (typically a warehouse) to a destination (typically a server).
    """

    __slots__ = ("cargo", "destination", "origin")

    def __init__(
        self,
        *,
        env: Environment,
        origin: Server,
        destination: Server,
        cargo: dict[str, int],
        processing_times: Sequence[float] | None = None,
        due_date: SimTime | None = None,
        priority_policy: Callable[[Any, Server], float] | None = None,
    ) -> None:
        """Initialize a transport job.

        Args:
            env: The simulation environment.
            origin: Source location (e.g., Warehouse).
            destination: Target location (e.g., production Server).
            cargo: Materials being transported {product_name: quantity}.
            processing_times: Optional processing times (defaults to [0] for single-hop transport).
            due_date: Optional due date (defaults to infinity if not specified).
            priority_policy: Optional function to compute priority.
        """
        # Transport jobs have a simple routing: just the AGV that will carry them
        # The actual servers list will be set when assigned to an AGV
        super().__init__(
            env=env,
            job_type=JobType.TRANSPORT,
            sku="transport",
            servers=[origin] if processing_times is None else [origin],
            processing_times=processing_times or [0.0],
            due_date=due_date if due_date is not None else float("inf"),
            priority_policy=priority_policy,
        )
        self.origin = origin
        self.destination = destination
        self.cargo = cargo

    def __repr__(self) -> str:
        return f"TransportJob(id='{self.id}', cargo={self.cargo})"


class WarehouseJob(BaseJob):
    """A warehouse job for pick or put operations.

    Warehouse jobs represent individual pick or put operations at a warehouse store.
    They are typically created by the MaterialCoordinator to fulfill material requirements.
    """

    __slots__ = ("operation_type", "product", "quantity")

    def __init__(
        self,
        *,
        env: Environment,
        warehouse: Server,
        product: str,
        quantity: int,
        operation_type: str,
        processing_time: float = 0.0,
        due_date: SimTime | None = None,
        priority_policy: Callable[[Any, Server], float] | None = None,
    ) -> None:
        """Initialize a warehouse job.

        Args:
            env: The simulation environment.
            warehouse: The Warehouse where the operation occurs.
            product: The product being picked or put.
            quantity: The quantity to pick or put.
            operation_type: Either "pick" or "put".
            processing_time: Time to complete the operation (pick/put time).
            due_date: Optional due date (defaults to infinity if not specified).
            priority_policy: Optional function to compute priority.
        """
        if operation_type not in ("pick", "put"):
            raise ValueError(f"operation_type must be 'pick' or 'put', got '{operation_type}'")

        super().__init__(
            env=env,
            job_type=JobType.WAREHOUSE,
            sku=f"warehouse_{operation_type}",
            servers=[warehouse],
            processing_times=[processing_time],
            due_date=due_date if due_date is not None else float("inf"),
            priority_policy=priority_policy,
        )
        self.product = product
        self.quantity = quantity
        self.operation_type = operation_type

    def __repr__(self) -> str:
        return f"WarehouseJob(id='{self.id}', {self.operation_type} {self.quantity}x {self.product})"
