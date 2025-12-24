"""Job management and scheduling logic for jobshop simulations."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from simulatte.environment import Environment

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Iterable, Sequence

    from simpy.core import SimTime

    from simulatte.server import Server


class JobType(Enum):
    """Enumeration of job types in the simulation."""

    PRODUCTION = auto()
    TRANSPORT = auto()
    WAREHOUSE = auto()


class BaseJob(ABC):
    """Abstract base class for jobs flowing through the simulation.

    This class defines the common interface and state tracking for all job types.
    Concrete implementations (ProductionJob, TransportJob, WarehouseJob) extend this
    with type-specific attributes and behavior.
    """

    __slots__ = (
        "_env",
        "_processing_times",
        "_servers",
        "created_at",
        "current_server",
        "done",
        "due_date",
        "family",
        "finished_at",
        "id",
        "job_type",
        "priority_policy",
        "psp_exit_at",
        "release_evaluations",
        "rework",
        "routing",
        "servers_entry_at",
        "servers_exit_at",
    )

    def __init__(
        self,
        *,
        env: Environment,
        job_type: JobType,
        family: str,
        servers: Sequence[Server],
        processing_times: Sequence[float],
        due_date: SimTime,
        priority_policy: Callable[[Any, Server], float] | None = None,
    ) -> None:
        self._env = env
        self.job_type = job_type
        self.id = str(uuid.uuid4())
        self.family = family
        self._servers = servers
        self._processing_times = processing_times
        self.due_date = due_date
        self.priority_policy = priority_policy

        self.routing: dict[Server, float] = dict(zip(self._servers, self._processing_times, strict=True))
        self.current_server: Server | None = None

        self.rework = False
        self.done = False
        self.release_evaluations = 0

        self.created_at: float = env.now
        self.psp_exit_at: SimTime | None = None
        self.servers_entry_at: dict[Server, SimTime | None] = dict.fromkeys(self._servers)
        self.servers_exit_at: dict[Server, SimTime | None] = dict.fromkeys(self._servers)
        self.finished_at: SimTime | None = None

    @abstractmethod
    def __repr__(self) -> str:
        """Return a string representation of the job."""

    @property
    def makespan(self) -> float:
        if self.finished_at is not None:
            return self.finished_at - self.created_at
        return self._env.now - self.created_at

    @property
    def processing_times(self) -> tuple[float, ...]:
        return tuple(self._processing_times)

    @property
    def servers(self) -> tuple[Server, ...]:
        return tuple(self._servers)

    @property
    def server_processing_times(self) -> Iterable[tuple[Server, float]]:
        yield from zip(self._servers, self._processing_times, strict=False)

    @property
    def server_waiting_times(self) -> dict[Server, float | None]:
        waiting_times = {}
        for server, processing_time in self.server_processing_times:
            entry_at = self.servers_entry_at[server]
            exit_at = self.servers_exit_at[server]
            if entry_at is not None and exit_at is not None:
                waiting_times[server] = exit_at - entry_at - processing_time
            elif entry_at is not None:
                waiting_times[server] = self._env.now - entry_at
            else:
                waiting_times[server] = None
        return waiting_times

    @property
    def total_queue_time(self) -> float:
        if not self.done:
            raise ValueError("Job is not done. Cannot calculate total queue time.")

        waiting_times = self.server_waiting_times
        if None in waiting_times.values():
            raise ValueError("Job has missing timing information. Cannot calculate total queue time.")

        return sum(wt for wt in waiting_times.values() if wt is not None)

    @property
    def slack_time(self) -> float:
        return self.due_date - self._env.now

    @property
    def time_in_system(self) -> float:
        last_server = self._servers[-1]
        first_server = self._servers[0]

        if (
            self.done
            and (end := self.servers_exit_at.get(last_server)) is not None
            and (start := self.servers_entry_at.get(first_server)) is not None
        ):
            return end - start

        raise ValueError("Job is not done or missing timestamps.")

    @property
    def time_in_shopfloor(self) -> float:
        return self.time_in_system

    @property
    def late(self) -> bool:
        if self.done:
            return self.finished_at is not None and self.finished_at > self.due_date
        return self._env.now > self.due_date

    @property
    def is_in_psp(self) -> bool:
        return self.psp_exit_at is None

    @property
    def time_in_psp(self) -> float:
        if self.psp_exit_at is None:
            raise ValueError("Job has not been released from PSP. Cannot calculate time in PSP.")
        return self.psp_exit_at - self.created_at

    @property
    def remaining_routing(self) -> tuple[Server, ...]:
        return tuple(srv for srv in self._servers if self.servers_entry_at[srv] is None)

    @property
    def next_server(self) -> Server | None:
        if not self.is_in_psp:
            return next((srv for srv in self.servers_entry_at if self.servers_entry_at[srv] is None), None)
        return self.servers[0]

    @property
    def previous_server(self) -> Server | None:
        for server in reversed(self.servers_exit_at.keys()):
            if self.servers_exit_at[server] is not None:
                return server
        return None

    @property
    def lateness(self) -> float:
        if self.done and self.finished_at is not None:
            return self.finished_at - self.due_date
        raise ValueError("Job is not done or missing finish time. Cannot calculate lateness.")

    @property
    def planned_slack_time(self) -> float:
        return self.slack_time - sum(self._processing_times)

    @property
    def virtual_lateness(self) -> float:
        return self._env.now - self.due_date

    @property
    def virtual_tardy(self) -> bool:
        return self.virtual_lateness > 0

    @property
    def virtual_early(self) -> bool:
        return self.virtual_lateness < 0

    @property
    def virtual_in_window(self) -> bool:
        return self.would_be_finished_in_due_date_window(allowance=7)

    def is_finished_in_due_date_window(self, window_size: float = 7) -> bool:
        if self.done and self.finished_at is not None:
            return self.due_date - window_size <= self.finished_at <= self.due_date + window_size
        raise ValueError("Job is not done or missing finish time. Cannot determine if finished in due date window.")

    def planned_release_date(self, allowance: int = 2) -> SimTime:
        return self.due_date - (sum(self._processing_times) + len(self._servers) * allowance)

    def starts_at(self, server: Server) -> bool:
        return self._servers[0] is server

    def planned_slack_times(self, allowance: float = 0) -> dict[Server, float | None]:
        slack_times = {}
        pst = self.slack_time
        for server, processing_time in reversed(list(self.server_processing_times)):
            pst -= processing_time + allowance
            slack_times[server] = pst
        for server, exit_time in self.servers_exit_at.items():
            if exit_time is not None:
                slack_times[server] = None
        return slack_times

    def priority(self, server: Server) -> float:
        if self.priority_policy is not None:
            return self.priority_policy(self, server)
        return 0

    def would_be_finished_in_due_date_window(self, allowance: float = 7) -> bool:
        return self.due_date - allowance <= self._env.now <= self.due_date + allowance


class ProductionJob(BaseJob):
    """A production job that flows through servers with optional material requirements.

    Production jobs represent manufacturing orders that require processing at one or more
    servers. They can optionally specify material requirements that must be delivered
    before processing can begin at each operation.
    """

    __slots__ = ("material_requirements",)

    def __init__(
        self,
        *,
        env: Environment,
        family: str,
        servers: Sequence[Server],
        processing_times: Sequence[float],
        due_date: SimTime,
        priority_policy: Callable[[Any, Server], float] | None = None,
        material_requirements: dict[int, dict[str, int]] | None = None,
    ) -> None:
        """Initialize a production job.

        Args:
            env: The simulation environment.
            family: Job family identifier.
            servers: Sequence of servers in the job's routing.
            processing_times: Processing time at each server.
            due_date: Target completion time.
            priority_policy: Optional function to compute priority at each server.
            material_requirements: Optional mapping from operation index to required
                materials. Format: {op_index: {product_name: quantity}}.
                Example: {0: {"steel": 2, "bolts": 10}} means operation 0 requires
                2 units of steel and 10 bolts to be delivered before processing.
        """
        super().__init__(
            env=env,
            job_type=JobType.PRODUCTION,
            family=family,
            servers=servers,
            processing_times=processing_times,
            due_date=due_date,
            priority_policy=priority_policy,
        )
        self.material_requirements = material_requirements or {}

    def __repr__(self) -> str:
        return f"ProductionJob(id='{self.id}', family='{self.family}')"

    def get_materials_for_operation(self, op_index: int) -> dict[str, int]:
        """Get material requirements for a specific operation.

        Args:
            op_index: The operation index (0-based).

        Returns:
            Dictionary mapping product names to required quantities,
            or empty dict if no materials required.
        """
        return self.material_requirements.get(op_index, {})


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
            origin: Source location (e.g., WarehouseStore).
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
            family="transport",
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
            warehouse: The WarehouseStore where the operation occurs.
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
            family=f"warehouse_{operation_type}",
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
