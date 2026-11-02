"""Restricted production backend using explicit managed ownership and state hashing.

The opaque hash projection does not establish per-actor event tracing/replay.
"""

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Annotated, Literal

from simulatte.collectors import ShopFloorKPIs
from simulatte.model import Range
from simulatte._wire import canonical_pack, freeze
from simulatte.execution.backend import BackendCapabilities, ExecutionBackend
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


def _digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


class ExecutionAdapter(ExecutionBackend):
    kpis = ShopFloorKPIs.kpis

    def __init__(self, env, layout, params, data, *, generation_s=0):
        if type(params) is not Parameters or type(data) is not Workload or data.servers != params.servers:
            raise ValueError("unsupported parameters or workload")
        self.env, self.params, self.data = env, params, data
        self.closed = self.finished = False
        self.stream = self._snapshot = self._states = self._state = None
        self.handles, self.generated, self.decisions = [], [], []
        self.steps = 0
        self.capabilities = BackendCapabilities(
            params.backend + "-production",
            "native-production-state-v1" if params.backend == "native" else "production-domain-state-v1",
        )
        env.attach_execution_backend(self)

    def prepare(self, context):
        self.stream = FACTORIES[self.params.backend](
            self.params.servers,
            wip_cap=self.params.wip_cap,
            policy=self.params.policy,
            warmup=context.stopping.warmup,
        )
        self._templates_digest = _digest([(j.arrival, j.route, j.durations, j.due) for j in self.data.jobs])
        context.env.process(self.produce())

    def advance(self, until):
        if self.closed or self.finished:
            raise RuntimeError("backend is closed or finalized")
        self.stream.advance(until)
        self.steps += 1

    def produce(self):
        for index, base in enumerate(self.data.jobs):
            yield self.env.timeout(base.arrival - self.env.now)
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

    def scalars(self):
        if self._snapshot is not None:
            return dict(window=self._snapshot["window"], scalars=dict(self._snapshot["scalars"]))
        if self.closed:
            raise RuntimeError("backend closed after a failure")
        if self.stream.now != self.env.now:
            raise RuntimeError("unsupported observation outside a synchronized execution boundary")
        return self.stream.snapshot()

    def kpi_values(self):
        return {key.partition("/")[2]: value for key, value in self.scalars()["scalars"].items()}

    def states(self):
        return self._states if self.closed else [self.stream.inspect(handle) for handle in self.handles]

    def state_digest(self):
        if self._state is not None:
            return self._state
        if self.closed:
            raise RuntimeError("backend closed after a failure")
        if self.stream.now != self.env.now:
            raise RuntimeError("unsupported state observation outside a synchronized boundary")
        if self.params.backend == "native":
            engine = {"schema": "NSTA0001", "sha256": hashlib.sha256(self.stream.state_bytes()).hexdigest()}
        else:
            # This oracle schema is a domain projection, not a serialization of
            # Python generator frames or a cross-simulator state identity.
            engine = dict(
                now=float(self.stream.now),
                entities=self.stream.env.entities.snapshot(include_presentation=False),
                starts=self.stream.starts,
                ends=self.stream.ends,
                dispatch=self.stream.dispatch,
                jobs=self.states(),
            )
        state = dict(
            schema=self.capabilities.state_schema,
            engine=engine,
            templates=self._templates_digest,
            config={"servers": self.params.servers, "wip_cap": self.params.wip_cap, "policy": self.params.policy},
            producer=self.params.producer,
            admitted=len(self.handles),
            generated=_digest([(j.arrival, j.route, j.durations, j.due) for j in self.generated]),
            decisions=_digest(self.decisions),
        )
        return hashlib.sha256(canonical_pack(freeze(state))).hexdigest()

    def finish(self):
        if self.closed or self.finished:
            raise RuntimeError("backend is closed or finalized")
        self._state = self.state_digest()
        self._snapshot = self.scalars()
        if self.params.verify:
            self._states = self.states()
        self.finished = True
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
                backend_state={"schema": self.capabilities.state_schema, "digest": self._state},
            )
            (self.env.artifacts_dir / "native-scalar-result.json").write_text(json.dumps(result) + "\n")

    def close(self):
        if self.closed:
            return
        try:
            if self.stream is not None and self._snapshot is None:
                self._snapshot = self.scalars()
        finally:
            self.closed = True
            if self.stream is not None:
                self.stream.close()
