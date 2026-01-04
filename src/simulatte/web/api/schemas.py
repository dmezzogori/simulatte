"""Pydantic schemas for API requests and responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SimulationConfig(BaseModel):
    """Configuration for running a simulation."""

    until: float = Field(..., description="Simulation end time", gt=0)
    seed: int | None = Field(None, description="Random seed for reproducibility")
    snapshot_interval: float = Field(10.0, description="Interval between snapshots", gt=0)


class MultiRunConfig(BaseModel):
    """Configuration for running multiple simulations."""

    until: float = Field(..., description="Simulation end time", gt=0)
    seeds: list[int] = Field(..., description="List of random seeds for each run", min_length=1)
    snapshot_interval: float = Field(10.0, description="Interval between snapshots", gt=0)


class SimulationStatus(BaseModel):
    """Current status of a simulation run."""

    run_id: int | None = Field(None, description="Current run ID")
    state: Literal["idle", "running", "completed", "error"] = Field(..., description="Current state")
    progress: float = Field(0.0, description="Progress from 0.0 to 1.0", ge=0.0, le=1.0)
    current_time: float = Field(0.0, description="Current simulation time")
    until_time: float | None = Field(None, description="Target simulation time")
    error_message: str | None = Field(None, description="Error message if state is 'error'")


class SimulationInfo(BaseModel):
    """Information about the loaded simulation."""

    module_name: str = Field(..., description="Name of the simulation module")
    server_count: int | None = Field(None, description="Number of servers (after setup)")
    has_psp: bool | None = Field(None, description="Whether simulation has Pre-Shop Pool")
    has_router: bool | None = Field(None, description="Whether simulation has Router")


class ServerState(BaseModel):
    """State of a single server at a point in time."""

    id: int = Field(..., description="Server index")
    queue_length: int = Field(..., description="Number of jobs in queue")
    processing_job: str | None = Field(None, description="ID of job being processed")
    utilization: float = Field(..., description="Current utilization rate", ge=0.0)
    wip: float = Field(..., description="Work-in-progress at this server", ge=0.0)


class JobState(BaseModel):
    """State of a single job at a point in time."""

    id: str = Field(..., description="Job UUID")
    sku: str = Field(..., description="Stock keeping unit")
    location: Literal["psp", "queue", "processing", "transit", "completed"] = Field(..., description="Current location")
    server_id: int | None = Field(None, description="Current or target server index")
    queue_position: int | None = Field(None, description="Position in queue (if queued)")
    urgency: float = Field(..., description="Urgency level from 0.0 to 1.0", ge=0.0, le=1.0)
    due_date: float = Field(..., description="Due date time")
    created_at: float = Field(..., description="Creation time")
    color: str = Field(..., description="Hex color for visualization")


class SnapshotData(BaseModel):
    """Full snapshot of simulation state at a point in time."""

    id: int = Field(..., description="Snapshot ID")
    sim_time: float = Field(..., description="Simulation time")
    servers: list[ServerState] = Field(..., description="State of all servers")
    jobs: list[JobState] = Field(..., description="State of all active jobs")
    psp_jobs: list[str] = Field(default_factory=list, description="Job IDs in Pre-Shop Pool")
    wip_total: float = Field(..., description="Total work-in-progress")
    wip_per_server: dict[str, float] = Field(..., description="WIP per server (string keys)")
    jobs_completed: int = Field(..., description="Total jobs completed so far")


class SnapshotListItem(BaseModel):
    """Summary of a snapshot for listing."""

    id: int
    sim_time: float
    job_count: int
    wip_total: float


class SnapshotListResponse(BaseModel):
    """Response for listing snapshots."""

    run_id: int
    total: int
    snapshots: list[SnapshotListItem]


class AnalyticsSummary(BaseModel):
    """Summary analytics for a completed simulation."""

    run_id: int
    total_jobs: int = Field(..., description="Total jobs created")
    completed_jobs: int = Field(..., description="Jobs that completed")
    avg_makespan: float = Field(..., description="Average job makespan")
    avg_tardiness: float = Field(..., description="Average tardiness (negative = early)")
    on_time_rate: float = Field(..., description="Fraction of jobs on time", ge=0.0, le=1.0)
    tardy_rate: float = Field(..., description="Fraction of jobs that were late", ge=0.0, le=1.0)
    early_rate: float = Field(..., description="Fraction of jobs that were early", ge=0.0, le=1.0)
    avg_wip: float = Field(..., description="Average total WIP")
    max_wip: float = Field(..., description="Maximum WIP observed")
    server_utilizations: dict[str, float] = Field(..., description="Utilization per server")
    avg_queue_time: float = Field(..., description="Average total queue time per job")


class TimeSeriesPoint(BaseModel):
    """Single point in a time series."""

    time: float
    value: float


class TimeSeriesResponse(BaseModel):
    """Response containing time series data."""

    run_id: int
    metric: str
    data: list[TimeSeriesPoint]


class PreferencesResponse(BaseModel):
    """User preferences."""

    theme: Literal["light", "dark", "system"] = Field("system", description="Color theme")


class PreferencesUpdate(BaseModel):
    """Update user preferences."""

    theme: Literal["light", "dark", "system"] | None = None


class ErrorResponse(BaseModel):
    """Error response."""

    error: str = Field(..., description="Error message")
    detail: str | None = Field(None, description="Additional details")


class SuccessResponse(BaseModel):
    """Generic success response."""

    success: bool = True
    message: str | None = None
