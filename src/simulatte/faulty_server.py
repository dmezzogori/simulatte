"""Server that can break down during processing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import simpy

from simulatte.environment import Environment
from simulatte.server import Server

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from simulatte.job import BaseJob
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator


class FaultyServer(Server):
    """A server that experiences random breakdowns and repairs."""

    def __init__(
        self,
        *,
        env: Environment,
        capacity: int,
        time_between_failures_distribution: Callable[[], float],
        repair_time_distribution: Callable[[], float],
        shopfloor: ShopFloor | None = None,
        collect_time_series: bool = False,
        retain_job_history: bool = False,
    ) -> None:
        super().__init__(
            env=env,
            capacity=capacity,
            shopfloor=shopfloor,
            collect_time_series=collect_time_series,
            retain_job_history=retain_job_history,
        )
        self.repair_time_distribution = repair_time_distribution
        self.time_between_failures_distribution = time_between_failures_distribution

        self.breakdown_event = simpy.Event(self.env)
        self.breakdown_time = 0
        self.breakdowns = 0

        self.env.process(self.breakdown_process())

    def breakdown_process(self) -> ProcessGenerator:
        while True:
            time_between_failures = self.time_between_failures_distribution()
            yield self.env.timeout(time_between_failures)
            if not self.breakdown_event.triggered:
                self.breakdowns += 1
                self.breakdown_event.succeed()

    def _process_or_breakdown(self, processing_time: float) -> ProcessGenerator:
        start_time = self.env.now
        res = yield self.env.timeout(processing_time) | self.breakdown_event

        if self.breakdown_event in res:
            remaining_service_time = processing_time - (self.env.now - start_time)
            repair_timeout = self.repair_time_distribution()
            yield self.env.timeout(repair_timeout)
            self.breakdown_time += repair_timeout
            self.breakdown_event = simpy.Event(self.env)
            yield self.env.process(self._process_or_breakdown(remaining_service_time))

    def process_job(self, job: BaseJob, processing_time: float) -> ProcessGenerator:
        if self._jobs is not None:
            self._jobs.append(job)
        yield self.env.process(self._process_or_breakdown(processing_time))
        self.worked_time += processing_time
