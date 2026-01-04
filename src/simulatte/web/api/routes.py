"""API routes for Simulatte Web UI."""

from __future__ import annotations

import threading
import traceback
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from simulatte.web.api.schemas import (
    AnalyticsSummary,
    MultiRunConfig,
    PreferencesResponse,
    PreferencesUpdate,
    SimulationConfig,
    SimulationInfo,
    SimulationStatus,
    SnapshotData,
    SnapshotListItem,
    SnapshotListResponse,
    SuccessResponse,
    TimeSeriesPoint,
    TimeSeriesResponse,
)
from simulatte.web.db import models

router = APIRouter()

# Module-level state for tracking current simulation
_current_run_id: int | None = None
_current_state: str = "idle"
_current_progress: float = 0.0
_current_time: float = 0.0
_until_time: float | None = None
_error_message: str | None = None
_simulation_thread: threading.Thread | None = None
_stop_requested: bool = False


def _reset_state() -> None:
    """Reset the simulation state."""
    global _current_run_id, _current_state, _current_progress
    global _current_time, _until_time, _error_message, _stop_requested
    _current_run_id = None
    _current_state = "idle"
    _current_progress = 0.0
    _current_time = 0.0
    _until_time = None
    _error_message = None
    _stop_requested = False


@router.get("/simulation/info", response_model=SimulationInfo)
async def get_simulation_info() -> SimulationInfo:
    """Get information about the loaded simulation."""
    from simulatte.web.app import get_simulation_class

    try:
        sim_class = get_simulation_class()
        return SimulationInfo(
            module_name=sim_class.__name__,
            server_count=None,  # Only known after setup
            has_psp=None,
            has_router=None,
        )
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Simulation class not loaded")


@router.get("/simulation/status", response_model=SimulationStatus)
async def get_simulation_status() -> SimulationStatus:
    """Get the current status of simulation."""
    return SimulationStatus(
        run_id=_current_run_id,
        state=_current_state,  # type: ignore[arg-type]
        progress=_current_progress,
        current_time=_current_time,
        until_time=_until_time,
        error_message=_error_message,
    )


@router.post("/simulation/run", response_model=SimulationStatus)
async def start_simulation(
    config: SimulationConfig,
    background_tasks: BackgroundTasks,
) -> SimulationStatus:
    """Start a single simulation run."""
    global _current_state, _simulation_thread

    if _current_state == "running":
        raise HTTPException(status_code=409, detail="Simulation already running")

    # Start simulation in background
    background_tasks.add_task(_run_simulation, config)

    return SimulationStatus(
        run_id=None,
        state="running",
        progress=0.0,
        current_time=0.0,
        until_time=config.until,
        error_message=None,
    )


@router.post("/simulation/run-multiple", response_model=SimulationStatus)
async def start_multiple_simulations(
    config: MultiRunConfig,
    background_tasks: BackgroundTasks,
) -> SimulationStatus:
    """Start multiple simulation runs (analytics only, no visualization)."""
    global _current_state

    if _current_state == "running":
        raise HTTPException(status_code=409, detail="Simulation already running")

    # Start simulations in background
    background_tasks.add_task(_run_multiple_simulations, config)

    return SimulationStatus(
        run_id=None,
        state="running",
        progress=0.0,
        current_time=0.0,
        until_time=config.until,
        error_message=None,
    )


@router.post("/simulation/stop", response_model=SuccessResponse)
async def stop_simulation() -> SuccessResponse:
    """Stop the currently running simulation."""
    global _stop_requested

    if _current_state != "running":
        raise HTTPException(status_code=409, detail="No simulation running")

    _stop_requested = True
    return SuccessResponse(message="Stop requested")


def _run_simulation(config: SimulationConfig) -> None:
    """Run a simulation (called in background thread)."""
    global _current_run_id, _current_state, _current_progress
    global _current_time, _until_time, _error_message, _stop_requested

    _reset_state()
    _current_state = "running"
    _until_time = config.until

    try:
        from simulatte.environment import Environment
        from simulatte.web.app import get_simulation_class
        from simulatte.web.snapshot import SnapshotCollector

        # Create run record
        _current_run_id = models.create_run(
            env_id="",
            until_time=config.until,
            seed=config.seed,
            config=config.model_dump(),
        )
        models.update_run_status(_current_run_id, "running")

        # Create simulation
        sim_class = get_simulation_class()
        sim = sim_class()

        # Create environment with db persistence
        env = Environment()

        # Setup simulation
        components = sim.setup(env)

        # Create snapshot collector
        collector = SnapshotCollector(
            run_id=_current_run_id,
            shopfloor=components.shopfloor,
            servers=components.servers,
            psp=components.psp,
            interval=config.snapshot_interval,
        )

        # Register progress callback
        def update_progress() -> None:
            global _current_progress, _current_time
            if config.until > 0:
                _current_progress = min(env.now / config.until, 1.0)
                _current_time = env.now
                models.update_run_status(_current_run_id, "running", progress=_current_progress)

        # Run simulation with periodic updates
        step_size = config.until / 100  # Update progress roughly 100 times
        current_target = step_size

        while env.now < config.until and not _stop_requested:
            # Step simulation
            try:
                env.run(until=min(current_target, config.until))
            except Exception:
                # No more events, simulation complete
                break

            # Capture snapshot if needed
            collector.maybe_capture(env.now)

            # Update progress
            update_progress()
            current_target += step_size

        # Final snapshot
        collector.capture(env.now)

        # Compute and cache analytics
        _cache_run_analytics(_current_run_id, components)

        if _stop_requested:
            models.update_run_status(_current_run_id, "stopped")
            _current_state = "idle"
        else:
            models.update_run_status(_current_run_id, "completed")
            _current_state = "completed"
            _current_progress = 1.0

    except Exception as e:
        _error_message = str(e)
        _current_state = "error"
        if _current_run_id:
            models.update_run_status(_current_run_id, "error", error_message=traceback.format_exc())


def _run_multiple_simulations(config: MultiRunConfig) -> None:
    """Run multiple simulations (called in background thread)."""
    global _current_run_id, _current_state, _current_progress
    global _current_time, _until_time, _error_message

    _reset_state()
    _current_state = "running"
    _until_time = config.until

    try:
        from simulatte.environment import Environment
        from simulatte.web.app import get_simulation_class

        total_runs = len(config.seeds)
        sim_class = get_simulation_class()

        for i, seed in enumerate(config.seeds):
            if _stop_requested:
                break

            # Create run record
            run_id = models.create_run(
                env_id="",
                until_time=config.until,
                seed=seed,
                config=config.model_dump(),
            )
            _current_run_id = run_id
            models.update_run_status(run_id, "running")

            # Run simulation
            sim = sim_class()
            env = Environment()
            components = sim.setup(env)

            # Run without snapshots for multi-run (faster)
            sim.run(until=config.until)

            # Cache analytics
            _cache_run_analytics(run_id, components)
            models.update_run_status(run_id, "completed")

            # Update overall progress
            _current_progress = (i + 1) / total_runs

        _current_state = "completed"
        _current_progress = 1.0

    except Exception as e:
        _error_message = str(e)
        _current_state = "error"
        if _current_run_id:
            models.update_run_status(_current_run_id, "error", error_message=traceback.format_exc())


def _cache_run_analytics(run_id: int, components: Any) -> None:
    """Cache analytics for a completed run."""
    shopfloor = components.shopfloor
    servers = components.servers

    # Get completed jobs
    jobs_done = list(shopfloor.jobs_done)
    if not jobs_done:
        return

    # Compute metrics
    makespans = [j.makespan for j in jobs_done]
    avg_makespan = sum(makespans) / len(makespans)

    tardiness = [j.lateness for j in jobs_done]
    avg_tardiness = sum(tardiness) / len(tardiness)

    tardy_count = sum(1 for j in jobs_done if j.lateness > 0)
    early_count = sum(1 for j in jobs_done if j.lateness < 0)
    on_time_count = sum(1 for j in jobs_done if j.is_finished_in_due_date_window())

    queue_times = [j.total_queue_time for j in jobs_done]
    avg_queue_time = sum(queue_times) / len(queue_times)

    # Cache metrics
    models.cache_analytics(run_id, "total_jobs", len(jobs_done) + len(shopfloor.jobs))
    models.cache_analytics(run_id, "completed_jobs", len(jobs_done))
    models.cache_analytics(run_id, "avg_makespan", avg_makespan)
    models.cache_analytics(run_id, "avg_tardiness", avg_tardiness)
    models.cache_analytics(run_id, "tardy_rate", tardy_count / len(jobs_done))
    models.cache_analytics(run_id, "early_rate", early_count / len(jobs_done))
    models.cache_analytics(run_id, "on_time_rate", on_time_count / len(jobs_done))
    models.cache_analytics(run_id, "avg_queue_time", avg_queue_time)
    models.cache_analytics(run_id, "max_wip", shopfloor.maximum_wip_value)

    # Server utilizations
    for i, server in enumerate(servers):
        models.cache_analytics(run_id, f"server_{i}_utilization", server.utilization_rate)


@router.get("/snapshots", response_model=SnapshotListResponse)
async def list_snapshots(
    run_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> SnapshotListResponse:
    """List snapshots for a simulation run."""
    # Use provided run_id or get latest
    if run_id is None:
        run = models.get_latest_run()
        if run is None:
            return SnapshotListResponse(run_id=0, total=0, snapshots=[])
        run_id = run.id

    total = models.get_snapshot_count(run_id)
    snapshots = models.get_snapshots(run_id, limit=limit, offset=offset)

    items = []
    for snap in snapshots:
        state = snap.state
        items.append(
            SnapshotListItem(
                id=snap.id,
                sim_time=snap.sim_time,
                job_count=len(state.get("jobs", [])),
                wip_total=state.get("wip_total", 0.0),
            )
        )

    return SnapshotListResponse(run_id=run_id, total=total, snapshots=items)


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotData)
async def get_snapshot(snapshot_id: int) -> SnapshotData:
    """Get a specific snapshot by ID."""
    # Query snapshot directly
    from simulatte.web.db.models import get_connection

    conn = get_connection()
    cursor = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
    row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    snapshot = models.Snapshot.from_row(row)
    state = snapshot.state

    return SnapshotData(
        id=snapshot.id,
        sim_time=snapshot.sim_time,
        servers=state.get("servers", []),
        jobs=state.get("jobs", []),
        psp_jobs=state.get("psp_jobs", []),
        wip_total=state.get("wip_total", 0.0),
        wip_per_server=state.get("wip_per_server", {}),
        jobs_completed=state.get("jobs_completed", 0),
    )


@router.get("/analytics/summary", response_model=AnalyticsSummary)
async def get_analytics_summary(run_id: int | None = None) -> AnalyticsSummary:
    """Get analytics summary for a simulation run."""
    if run_id is None:
        run = models.get_latest_run()
        if run is None:
            raise HTTPException(status_code=404, detail="No simulation runs found")
        run_id = run.id

    cached = models.get_cached_analytics(run_id)
    if not cached:
        raise HTTPException(status_code=404, detail="No analytics available for this run")

    # Build server utilizations dict
    server_utils = {}
    for key, value in cached.items():
        if key.startswith("server_") and key.endswith("_utilization"):
            server_id = key.replace("server_", "").replace("_utilization", "")
            server_utils[server_id] = value

    return AnalyticsSummary(
        run_id=run_id,
        total_jobs=int(cached.get("total_jobs", 0)),
        completed_jobs=int(cached.get("completed_jobs", 0)),
        avg_makespan=cached.get("avg_makespan", 0.0),
        avg_tardiness=cached.get("avg_tardiness", 0.0),
        on_time_rate=cached.get("on_time_rate", 0.0),
        tardy_rate=cached.get("tardy_rate", 0.0),
        early_rate=cached.get("early_rate", 0.0),
        avg_wip=cached.get("avg_wip", 0.0),
        max_wip=cached.get("max_wip", 0.0),
        server_utilizations=server_utils,
        avg_queue_time=cached.get("avg_queue_time", 0.0),
    )


@router.get("/analytics/timeseries/{metric}", response_model=TimeSeriesResponse)
async def get_timeseries(metric: str, run_id: int | None = None) -> TimeSeriesResponse:
    """Get time series data for a specific metric."""
    if run_id is None:
        run = models.get_latest_run()
        if run is None:
            raise HTTPException(status_code=404, detail="No simulation runs found")
        run_id = run.id

    # Get snapshots and extract metric
    snapshots = models.get_snapshots(run_id)

    data = []
    for snap in snapshots:
        state = snap.state
        value = None

        if metric == "wip":
            value = state.get("wip_total", 0.0)
        elif metric == "job_count":
            value = len(state.get("jobs", []))
        elif metric == "jobs_completed":
            value = state.get("jobs_completed", 0)
        elif metric.startswith("server_") and metric.endswith("_queue"):
            server_id = int(metric.replace("server_", "").replace("_queue", ""))
            servers = state.get("servers", [])
            if server_id < len(servers):
                value = servers[server_id].get("queue_length", 0)
        elif metric.startswith("wip_server_"):
            server_id = metric.replace("wip_server_", "")
            wip_per_server = state.get("wip_per_server", {})
            value = wip_per_server.get(server_id, 0.0)

        if value is not None:
            data.append(TimeSeriesPoint(time=snap.sim_time, value=value))

    return TimeSeriesResponse(run_id=run_id, metric=metric, data=data)


@router.get("/preferences", response_model=PreferencesResponse)
async def get_preferences() -> PreferencesResponse:
    """Get user preferences."""
    theme = models.get_preference("theme", "system")
    return PreferencesResponse(theme=theme)  # type: ignore[arg-type]


@router.put("/preferences", response_model=PreferencesResponse)
async def update_preferences(prefs: PreferencesUpdate) -> PreferencesResponse:
    """Update user preferences."""
    if prefs.theme is not None:
        models.set_preference("theme", prefs.theme)

    return await get_preferences()
