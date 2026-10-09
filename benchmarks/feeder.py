"""Replay a pre-generated workload through a simulatte shop, identically in every version (spec §14).

The feeder bypasses the ``Router``: it builds a LumsCor job shop with constructors present in both
``simulatte==0.12.0`` and the current branch (``Environment``, ``ShopFloor``, ``Server``, ``PreShopPool``,
``LumsCor``, ``ProductionJob``), then a process waits until each job's arrival time and calls
``PreShopPool.add``. No random number is drawn during the run, so both versions do the same work; the job and
operation counts are checked against the workload.

``LumsCor`` sets the PST priority rule on the router it is given; the feeder passes a stub that only holds the
``priority_policies`` attribute and gives that rule to every job, as ``Router.generate_job`` does.

Observers are each version's defaults: the shop floor's default ``EMAMetricsCollector``, the environment's
``SimLogger`` at its default level and, on the branch, the event bus with no subscriber (mode ``none``). Modes
``digest`` and ``full`` (branch only) add ``env.enable_digest()`` or a ``TraceRecorder`` with default chunk
limits, created before the shop as a user would.
"""

from __future__ import annotations

import gc
import hashlib
import inspect
import json
import time
import types
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.lumscor import LumsCor
from simulatte.psp import PreShopPool
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

MODES = ("none", "digest", "full")

# LumsCor parameters of the G1 reference shop.
CHECK_TIMEOUT = 5.0
WL_NORM = 6.0
ALLOWANCE_FACTOR = 2

_ENV_ACCEPTS_SEED = "seed" in inspect.signature(Environment).parameters


@dataclass(frozen=True)
class Workload:
    """A loaded workload: the parsed JSON and the SHA-256 of the file."""

    path: str
    sha256: str
    servers: int
    horizon: float
    operations: int
    jobs: list[list[Any]]
    params: dict[str, Any]


@dataclass(frozen=True)
class RunResult:
    """One timed run: wall time of build + run + close, and what the run did."""

    wall_s: float
    jobs_fed: int
    operations_fed: int
    jobs_done: int
    operations_done: int
    subscribers: int
    digest: str | None
    trajectory: str
    """SHA-256 of every finished job's SKU, due date, pool exit and finish time, in completion order: equal across
    versions and interpreters when they did the same work."""


def load(path: str | Path) -> Workload:
    data = Path(path).read_bytes()
    raw = json.loads(data)
    if raw.get("format") != "simulatte-bench-workload-v1":
        raise ValueError(f"{path}: not a simulatte benchmark workload")
    return Workload(
        path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        servers=raw["servers"],
        horizon=raw["horizon"],
        operations=raw["operations"],
        jobs=raw["jobs"],
        params=raw["params"],
    )


def has_trace() -> bool:
    """Whether the installed simulatte has the SP1 digest and trace recorder."""
    return hasattr(Environment, "enable_digest")


def default_label() -> str:
    """``head`` for a simulatte with the SP1 trace support, else ``simulatte <version>``."""
    from importlib.metadata import version

    return "head" if has_trace() else f"simulatte {version('simulatte')}"


def _new_environment() -> Any:
    # The branch draws a seed from os.urandom when none is given; the feeder draws nothing, but a fixed seed keeps
    # the manifest stable. 0.12.0 has no seed parameter.
    return Environment(seed=0) if _ENV_ACCEPTS_SEED else Environment()


def _feed(env: Any, rows: list[list[Any]], servers: list[Any], psp: Any, priority: Any) -> Generator[Any, Any, None]:
    for arrival, sku, routing, times, due in rows:
        delay = arrival - env.now
        if delay > 0:
            yield env.timeout(delay)
        job = ProductionJob(
            env=env,
            sku=sku,
            servers=[servers[i] for i in routing],
            processing_times=times,
            due_date=due,
            priority_policy=priority,
        )
        psp.add(job)


def run(workload: Workload, *, mode: str = "none", trace_path: str | Path | None = None) -> RunResult:
    """Build the shop, replay `workload` until its horizon and close the environment, timing all of it.

    Raises `RuntimeError` if a job is still unfinished at the horizon or the counts disagree with the workload.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if mode != "none" and not has_trace():
        raise RuntimeError(f"mode {mode!r} needs the SP1 trace support, absent from this simulatte")
    if mode == "full" and trace_path is None:
        raise ValueError("mode 'full' needs a trace path")
    rows = workload.jobs
    gc.collect()
    start = time.perf_counter()
    env = _new_environment()
    if mode == "digest":
        env.enable_digest()
    elif mode == "full":
        from simulatte.trace import TraceRecorder

        assert trace_path is not None
        TraceRecorder(env, trace_path)
    shopfloor = ShopFloor(env=env)
    servers = [Server(env=env, capacity=1, shopfloor=shopfloor) for _ in range(workload.servers)]
    psp = PreShopPool(env=env, shopfloor=shopfloor)
    router = types.SimpleNamespace(priority_policies=None)
    LumsCor(
        shopfloor=shopfloor,
        psp=psp,
        router=router,  # ty: ignore[invalid-argument-type]  # LumsCor only sets router.priority_policies
        wl_norm=WL_NORM,
        check_timeout=CHECK_TIMEOUT,
        allowance_factor=ALLOWANCE_FACTOR,
    )
    env.process(_feed(env, rows, servers, psp, router.priority_policies))
    env.run(until=workload.horizon)
    env.close()
    wall = time.perf_counter() - start

    bus = getattr(env, "bus", None)
    subscribers = 0 if bus is None else len(bus._subscriptions)
    digest = env.fingerprint().digest if mode != "none" else None
    done = shopfloor.jobs_done
    trajectory = hashlib.sha256()
    for job in done:
        trajectory.update(f"{job.sku}|{job.due_date!r}|{job.psp_exit_at!r}|{job.finished_at!r}\n".encode())
    result = RunResult(
        wall_s=wall,
        jobs_fed=len(rows),
        operations_fed=sum(len(row[2]) for row in rows),
        jobs_done=len(done),
        operations_done=sum(len(job.routing) for job in done),
        subscribers=subscribers,
        digest=digest,
        trajectory=trajectory.hexdigest(),
    )
    if result.operations_fed != workload.operations:
        raise RuntimeError(f"fed {result.operations_fed} operations, the workload declares {workload.operations}")
    if result.jobs_done != result.jobs_fed or result.operations_done != result.operations_fed:
        raise RuntimeError(
            f"{result.jobs_done}/{result.jobs_fed} jobs and {result.operations_done}/{result.operations_fed} "
            f"operations finished by the horizon {workload.horizon}; regenerate the workload with a longer --drain"
        )
    if mode == "none" and subscribers:
        raise RuntimeError(f"mode 'none' must run without bus subscribers, found {subscribers}")
    return result
