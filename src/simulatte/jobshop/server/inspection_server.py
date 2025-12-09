"""Inspection server with optional rework hook."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .server import Server

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.jobshop.job import Job
    from simulatte.jobshop.typing import ProcessGenerator


class InspectionServer(Server):
    """Server that can trigger rework logic."""

    def rework(self, job: Job) -> Any:  # noqa: ANN401
        raise NotImplementedError

    def process_job(self, job: Job, processing_time: float) -> ProcessGenerator:
        yield self.env.process(super().process_job(job, processing_time))
        if job.rework:
            job.rework = False
            self.rework(job)
