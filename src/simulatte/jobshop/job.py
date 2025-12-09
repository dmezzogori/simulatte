"""Job management and scheduling logic for jobshop simulations."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from .environment import Environment

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Iterable, Sequence

    from simpy.core import SimTime

    from .server.server import Server


class Job:
    """Represents a job/order flowing through a sequence of servers."""

    __slots__ = (
        "_processing_times",
        "_servers",
        "created_at",
        "current_server",
        "done",
        "due_date",
        "family",
        "finished_at",
        "id",
        "priority_policy",
        "psp_exit_at",
        "release_evalutations",
        "rework",
        "routing",
        "servers_entry_at",
        "servers_exit_at",
    )

    def __init__(
        self,
        *,
        family: str,
        servers: Sequence[Server],
        processing_times: Sequence[float],
        due_date: SimTime,
        priority_policy: Callable[[Job, Server], float] | None = None,
    ) -> None:
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
        self.release_evalutations = 0

        self.created_at: float = Environment().now
        self.psp_exit_at: SimTime | None = None
        self.servers_entry_at: dict[Server, SimTime | None] = dict.fromkeys(self._servers)
        self.servers_exit_at: dict[Server, SimTime | None] = dict.fromkeys(self._servers)
        self.finished_at: SimTime | None = None

    def __repr__(self) -> str:
        return f"Job(id='{self.id}')"

    @property
    def makespan(self) -> float:
        if self.finished_at is not None:
            return self.finished_at - self.created_at
        return Environment().now - self.created_at

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
                waiting_times[server] = Environment().now - entry_at
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
        return self.due_date - Environment().now

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
        return Environment().now > self.due_date

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

    def is_finished_in_due_date_window(self, window_size: float = 7) -> bool:
        if self.done and self.finished_at is not None:
            return self.due_date - window_size <= self.finished_at <= self.due_date + window_size
        raise ValueError("Job is not done or missing finish time. Cannot determine if finished in due date window.")

    def planned_release_date(self, allowance: int = 2) -> SimTime:
        return self.due_date - (sum(self._processing_times) + len(self._servers) * allowance)

    def starts_at(self, server: Server) -> bool:
        return self._servers[0] is server

    @property
    def planned_slack_time(self) -> float:
        return self.slack_time - sum(self._processing_times)

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

    @property
    def virtual_lateness(self) -> float:
        return Environment().now - self.due_date

    def would_be_finished_in_due_date_window(self, allowance: float = 7) -> bool:
        return self.due_date - allowance <= Environment().now <= self.due_date + allowance

    @property
    def virtual_tardy(self) -> bool:
        return self.virtual_lateness > 0

    @property
    def virtual_early(self) -> bool:
        return self.virtual_lateness < 0

    @property
    def virtual_in_window(self) -> bool:
        return self.would_be_finished_in_due_date_window(allowance=7)
