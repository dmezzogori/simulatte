"""Server resource with queue and utilization tracking."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, cast

import matplotlib.pyplot as plt
import simpy
from simpy.resources.resource import PriorityRequest

from simulatte.environment import Environment
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

    from simpy.resources.resource import Release

    from simulatte.job import Job
    from simulatte.typing import ProcessGenerator


class ServerPriorityRequest(PriorityRequest):
    """Priority request that carries the job reference and priority key."""

    def __init__(self, resource: Server, job: Job, preempt: bool = True) -> None:  # noqa: FBT001, FBT002
        self.server = resource
        self.job = job
        self.preempt = preempt
        self.time = resource.env.now
        super().__init__(resource=resource, priority=int(job.priority(resource)), preempt=preempt)

    def __repr__(self) -> str:
        return f"ServerPriorityRequest(job={self.job}, server={self.server})"


class Server(simpy.PriorityResource):
    """A server/workstation with queue and utilization tracking."""

    def __init__(self, *, capacity: int) -> None:
        self.env = Environment()
        super().__init__(self.env, capacity)
        self.worked_time: float = 0

        self._queue_history: dict[int, float] = defaultdict(float)
        self._qt: list[tuple[float, int]] = []
        self._ut: list[tuple[float, int]] = [(0, 0)]

        self._last_queue_level: int = 0
        self._last_queue_level_timestamp: float = 0

        self._jobs: list[Job] = []

        ShopFloor.servers.append(self)
        self._idx = ShopFloor.servers.index(self)

    def __repr__(self) -> str:
        return f"Server(id={self._idx})"

    @property
    def empty(self) -> bool:
        return len(self.queue) == 0

    @property
    def average_queue_length(self) -> float:
        return sum(queue_length * time for queue_length, time in self._queue_history.items()) / self.env.now

    @property
    def utilization_rate(self) -> float:
        if self.env.now == 0:
            return 0
        return self.worked_time / self.env.now

    @property
    def idle_time(self) -> float:
        return self.env.now - self.worked_time

    @property
    def queueing_jobs(self) -> Iterable[Job]:
        return (request.job for request in self.queue)

    def _update_qt(self) -> None:
        self._qt.append((self.env.now, len(self.queue)))

    def _update_ut(self) -> None:
        status = int(self.count == 1 or len(self.queue) > 0)
        if self._ut and self._ut[-1][1] == status:
            return
        self._ut.append((self.env.now, status))

    def _update_queue_history(self, _: simpy.Event | None) -> None:
        self._queue_history[self._last_queue_level] += self.env.now - self._last_queue_level_timestamp
        self._last_queue_level_timestamp = self.env.now
        self._last_queue_level = len(self.queue)
        self._update_qt()

    def request(  # type: ignore[override]
        self,
        *,
        job: Job,
        preempt: bool = True,
    ) -> ServerPriorityRequest:
        request = ServerPriorityRequest(self, job, preempt=preempt)
        job.servers_entry_at[self] = self.env.now
        job.current_server = self

        self._update_queue_history(None)
        request.callbacks.append(self._update_queue_history)
        return request

    def release(self, request: ServerPriorityRequest) -> Release:  # type: ignore[override]
        release = super().release(request)  # type: ignore[arg-type]
        request.job.servers_exit_at[self] = self.env.now
        self._update_ut()
        return release

    def process_job(self, job: Job, processing_time: float) -> ProcessGenerator:
        self._jobs.append(job)
        yield self.env.timeout(processing_time)
        self.worked_time += processing_time

    def sort_queue(self) -> None:
        queue_list = cast(list, self.queue)
        queue_list.sort(key=lambda req: req.key)

    def plot_qt(self) -> None:  # pragma: no cover
        x, y = zip(*self._qt, strict=False)
        plt.step(x, y, where="pre")
        plt.fill_between(x, y, step="pre", alpha=1.0)
        plt.title(f"Q(t): {self} queue length over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("Queue Length")
        plt.show()

    def plot_ut(self) -> None:  # pragma: no cover
        ut = [*self._ut, (self.env.now, self._ut[-1][1])]
        x, y = zip(*ut, strict=False)
        plt.step(x, y, where="post")
        plt.fill_between(x, y, step="post", alpha=1.0)
        plt.title(f"U(t): {self} utilization over time")
        plt.xlabel("Simulation Time")
        plt.ylabel("Utilization rate")
        plt.show()
