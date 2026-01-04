"""Snapshot capture for simulation replay."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from simulatte.web.db import models

if TYPE_CHECKING:
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor


def _hash_to_color(s: str) -> str:
    """Hash a string to a consistent HSL color."""
    hash_val = sum(ord(c) for c in s)
    hue = hash_val % 360
    return f"hsl({hue}, 70%, 50%)"


def _calculate_urgency(job: Any, current_time: float) -> float:
    """Calculate urgency level for a job (0.0 = normal, 1.0 = critical).

    Urgency increases as the job approaches or passes its due date.
    """
    if job.done:
        return 0.0

    time_to_due = job.due_date - current_time
    total_time = job.due_date - job.created_at

    if total_time <= 0:
        return 1.0

    # Progress through the job's lifecycle
    progress = 1 - (time_to_due / total_time)

    # Urgency ramps up in the last 30% of time
    if progress < 0.7:
        return 0.0
    elif progress >= 1.0:
        return 1.0
    else:
        # Scale 0.7-1.0 progress to 0.0-1.0 urgency
        return (progress - 0.7) / 0.3


class SnapshotCollector:
    """Collects simulation snapshots at regular intervals.

    Snapshots capture the full state of the simulation for replay,
    including all servers, jobs, and aggregate metrics.
    """

    def __init__(
        self,
        run_id: int,
        shopfloor: ShopFloor,
        servers: tuple[Server, ...],
        psp: PreShopPool | None = None,
        interval: float = 10.0,
    ) -> None:
        """Initialize the snapshot collector.

        Args:
            run_id: Database run ID to associate snapshots with.
            shopfloor: The shopfloor to capture.
            servers: Tuple of servers to capture.
            psp: Optional Pre-Shop Pool to capture.
            interval: Time interval between automatic snapshots.
        """
        self.run_id = run_id
        self.shopfloor = shopfloor
        self.servers = servers
        self.psp = psp
        self.interval = interval
        self._last_capture_time: float = -interval  # Ensure first capture at t=0

    def maybe_capture(self, sim_time: float) -> bool:
        """Capture a snapshot if enough time has passed since the last one.

        Args:
            sim_time: Current simulation time.

        Returns:
            True if a snapshot was captured, False otherwise.
        """
        if sim_time - self._last_capture_time >= self.interval:
            self.capture(sim_time)
            return True
        return False

    def capture(self, sim_time: float) -> int:
        """Capture a snapshot at the current simulation time.

        Args:
            sim_time: Current simulation time.

        Returns:
            The ID of the created snapshot.
        """
        self._last_capture_time = sim_time
        state = self._build_state(sim_time)
        return models.insert_snapshot(self.run_id, sim_time, state)

    def _build_state(self, sim_time: float) -> dict[str, Any]:
        """Build the full state dictionary for a snapshot."""
        # Capture server states
        servers_state = []
        wip_per_server: dict[str, float] = {}

        for i, server in enumerate(self.servers):
            # Get current processing job
            processing_job = None
            if server.count > 0:
                # Find job being processed (first non-queued request)
                for job in self.shopfloor.jobs:
                    if job.current_server is server:
                        processing_job = job.id
                        break

            server_state = {
                "id": i,
                "queue_length": len(list(server.queueing_jobs)),
                "processing_job": processing_job,
                "utilization": server.utilization_rate,
                "wip": self.shopfloor.wip.get(server, 0.0),
            }
            servers_state.append(server_state)
            wip_per_server[str(i)] = self.shopfloor.wip.get(server, 0.0)

        # Capture job states
        jobs_state = []

        # Jobs in shopfloor (active)
        for job in self.shopfloor.jobs:
            location = "processing"
            server_id = None
            queue_position = None

            if job.current_server is not None:
                server_id = self.servers.index(job.current_server) if job.current_server in self.servers else None
                # Check if queued or processing
                if server_id is not None:
                    server = self.servers[server_id]
                    queueing = list(server.queueing_jobs)
                    if job in queueing:
                        location = "queue"
                        queue_position = queueing.index(job)
                    else:
                        location = "processing"

            job_state = {
                "id": job.id,
                "sku": job.sku,
                "location": location,
                "server_id": server_id,
                "queue_position": queue_position,
                "urgency": _calculate_urgency(job, sim_time),
                "due_date": job.due_date,
                "created_at": job.created_at,
                "color": _hash_to_color(job.sku),
            }
            jobs_state.append(job_state)

        # Jobs in PSP
        psp_jobs = []
        if self.psp is not None:
            for job in self.psp.jobs:
                psp_jobs.append(job.id)
                job_state = {
                    "id": job.id,
                    "sku": job.sku,
                    "location": "psp",
                    "server_id": None,
                    "queue_position": None,
                    "urgency": _calculate_urgency(job, sim_time),
                    "due_date": job.due_date,
                    "created_at": job.created_at,
                    "color": _hash_to_color(job.sku),
                }
                jobs_state.append(job_state)

        # Calculate totals
        wip_total = sum(wip_per_server.values())
        jobs_completed = len(self.shopfloor.jobs_done)

        return {
            "servers": servers_state,
            "jobs": jobs_state,
            "psp_jobs": psp_jobs,
            "wip_total": wip_total,
            "wip_per_server": wip_per_server,
            "jobs_completed": jobs_completed,
        }
