"""Restricted research adapter for unmodified managed experiment execution.

This is not an Environment replacement. Its shell has a scalar result scope,
not a projection of native actors. Full traces and replay are unsupported.
"""

from dataclasses import dataclass
import hashlib
import json
import math
import time
from typing import Annotated, Literal

from simulatte.collectors import ShopFloorKPIs
from simulatte.entities import Entity, StateSchema
from simulatte.kpi import Collector
from simulatte.model import Range
from simulatte.stopping import Horizon
from prototypes.native_engine.model import Job, Workload
from .native_kpis import NativeKPIStream, ProductionKPIStream

FACTORIES = {"native": NativeKPIStream, "production": ProductionKPIStream}


@dataclass(frozen=True)
class Parameters:
    backend: Literal["native", "production"] = "native"
    producer: Literal["independent", "feedback"] = "independent"
    jobs: Annotated[int, Range(0, 100000)] = 5000
    servers: Annotated[int, Range(1, 100)] = 10
    wip_cap: Annotated[int, Range(1, 100000)] = 12
    policy: Literal["fifo", "spt"] = "spt"
    load: Annotated[float, Range(0.1, 2)] = 0.95
    verify: bool = True

    def __post_init__(self):
        if (
            self.backend not in FACTORIES
            or self.producer not in ("independent", "feedback")
            or self.policy not in ("fifo", "spt")
        ):
            raise ValueError("unsupported backend, producer or dispatch policy")
        for name, low, high in (("jobs", 0, 100000), ("servers", 1, 100), ("wip_cap", 1, 100000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"unsupported {name}")
        if (
            type(self.verify) is not bool
            or type(self.load) not in (int, float)
            or not math.isfinite(self.load)
            or not 0.1 <= self.load <= 2
        ):
            raise ValueError("unsupported load or verification option")


def templates(env, params):
    rng, at, jobs = env.rng("native-execution/templates"), 0.0, []
    mean_operations = (params.servers + 1) / 2
    for _ in range(params.jobs):
        at += rng.expovariate(params.servers * params.load / mean_operations)
        route = tuple(rng.sample(range(params.servers), rng.randint(1, params.servers)))
        durations = tuple(rng.uniform(0.5, 1.5) for _ in route)
        jobs.append(Job(at, route, durations, at + rng.uniform(30, 45)))
    return Workload(params.servers, tuple(jobs))


class ResultScope(Entity, kind="execution_slice"):
    state_schema = StateSchema({})

    def __init__(self, env):
        env.entities.attach(self, name="shopfloor", label="Scalar execution result scope")


class ScalarBridge(Collector):
    kpis = ShopFloorKPIs.kpis

    def __init__(self, scope, adapter):
        super().__init__(scope)
        self.adapter = adapter

    def scalar_values(self):
        return {key.partition("/")[2]: value for key, value in self.adapter.scalars()["scalars"].items()}


def _digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


class ExecutionAdapter:
    def __init__(self, env, layout, params, data, *, generation_s=0):
        if type(params) is not Parameters or type(data) is not Workload or data.servers != params.servers:
            raise ValueError("unsupported parameters or workload")
        if type(env._execution_policy) is not Horizon:
            raise ValueError("unsupported stopping policy: Horizon required")
        if env.activated or env.now != 0 or env.collectors or env.entities.live() or env._queue:
            raise ValueError("unsupported nonempty execution environment")
        if "close" in env.__dict__:
            raise ValueError("unsupported environment lifecycle overrides")
        if layout.to_dict() != {"schema_version": "1.0"}:
            raise ValueError("unsupported layout: this slice requires an empty layout")
        if any(recorder._level != "kpi" for recorder in env._recorders):
            raise ValueError("unsupported full tracing: scalar KPI level required")
        if env.warmup != env._execution_policy.warmup:
            raise ValueError("unsupported warm-up mismatch")
        self.env, self.params, self.data = env, params, data
        self.closed, self._snapshot, self._states = False, None, None
        self.handles, self.generated, self.decisions = [], [], []
        self.times = dict(
            generation_s=generation_s, setup_s=0.0, advance_s=0.0, producer_s=0.0, export_s=0.0, close_s=0.0
        )
        self.steps = 0
        start = time.perf_counter()
        self.stream = FACTORIES[params.backend](
            params.servers, wip_cap=params.wip_cap, policy=params.policy, warmup=env.warmup
        )
        self.times["setup_s"] = time.perf_counter() - start
        self._step, self._close = None, env.close
        # Private research integration points; no production API changes.
        env.close = self.close
        env.on_activate(self.install_step)
        try:
            self.collector = ScalarBridge(ResultScope(env), self).attach(env)
            env.process(self.produce())
        except BaseException:
            self.close()
            raise

    def install_step(self):
        # The managed build guard temporarily shadows step and restores it on
        # exit. Install only at activation, after that guard has finished.
        if "step" in self.env.__dict__:
            raise ValueError("unsupported environment step override")
        self._step = self.env.step
        self.env.step = self.step

    def step(self):
        at = float(self.env.peek())
        if math.isfinite(at):
            started = time.perf_counter()
            try:
                self.stream.advance(at)
            finally:
                self.times["advance_s"] += time.perf_counter() - started
            self.steps += 1
        self._step()

    def produce(self):
        for index, base in enumerate(self.data.jobs):
            yield self.env.timeout(base.arrival - self.env.now)
            started = time.perf_counter()
            choice = "seed"
            decision = (None, None, None, None, None, choice)
            if self.params.producer == "feedback" and index:
                probe = max(0, index - 1 - index % 32)
                seen = self.stream.inspect(self.handles[probe])
                state = seen["state"]
                choice = "short" if state == "pool" else "long" if state in ("scheduled", "active") else "normal"
                decision = (
                    probe,
                    *(None if x is None else float(x) for x in (seen["arrival"], seen["released"], seen["finished"])),
                    state,
                    choice,
                )
            if self.params.producer == "feedback":
                if choice == "short":
                    n = max(1, len(base.route) // 2)
                    route, durations = base.route[::-1][:n], tuple(d * 0.75 for d in base.durations[::-1][:n])
                elif choice == "long":
                    route, durations = base.route, tuple(d * 1.125 for d in base.durations)
                else:
                    route, durations = base.route, base.durations
                job = Job(base.arrival, route, durations, base.due)
                self.decisions.append(decision)
            else:
                job = base
            self.handles.append(self.stream.add(job))
            self.generated.append(job)
            self.times["producer_s"] += time.perf_counter() - started

    def scalars(self):
        if self.closed:
            if self._snapshot is None:
                raise RuntimeError("backend closed after a failure")
            return dict(window=self._snapshot["window"], scalars=dict(self._snapshot["scalars"]))
        if self.stream.now != self.env.now:
            raise RuntimeError("unsupported observation outside a synchronized execution boundary")
        return self.stream.snapshot()

    def states(self):
        return self._states if self.closed else [self.stream.inspect(handle) for handle in self.handles]

    def close(self):
        if self.closed:
            return
        try:
            started = time.perf_counter()
            try:
                self._snapshot = self.scalars()
                if self.params.verify:
                    self._states = self.states()
            finally:
                self.times["export_s"] += time.perf_counter() - started
                started = time.perf_counter()
                self.stream.close()
                self.times["close_s"] = time.perf_counter() - started
                self.closed = True
            if self.env.artifacts_dir is not None:
                proof = None
                if self.params.verify:
                    proof = dict(
                        generated=_digest([(j.arrival, j.route, j.durations, j.due) for j in self.generated]),
                        decisions=_digest(self.decisions),
                        states=_digest(self._states),
                    )
                result = dict(
                    snapshot=self._snapshot,
                    proof=proof,
                    admitted=len(self.handles),
                    steps=self.steps,
                    backend=self.params.backend,
                )
                (self.env.artifacts_dir / "native-scalar-result.json").write_text(json.dumps(result) + "\n")
        finally:
            if self._step is not None:
                self.env.step = self._step
            self.env.close = self._close
            self._close()
