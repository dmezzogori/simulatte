"""Standalone installed model: bounded feedback controller and ordinary reference."""

from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

import simpy
from simulatte import model
from simulatte.collectors import ShopFloorKPIs
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import Deltas, DomainEvent, event_type
from simulatte.experimental.native import ExpiredHandleError, Job, NativeProduction


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def candidate(seed, index, at, choice):
    # Counter source: no pre-generated templates or hidden RNG state.
    x = (index + seed * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
    x ^= x >> 30
    x = x * 0xBF58476D1CE4E5B9 & ((1 << 64) - 1)
    x ^= x >> 27
    routes = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))
    route = routes[x % 6]
    durations = tuple((2 + (x >> (4 * k)) % 4) / 4 for k in range(3))
    if choice == "short":
        route, durations = route[::-1][:2], tuple(v * 0.5 for v in durations[::-1][:2])
    return Job(float(at), route, durations, float(at + ((x >> 16) % 17 - 4)))


def job_wire(job):
    return dict(arrival=job.arrival, route=job.route, durations=job.durations, due=job.due)


def wake(env, at, priority=0, callback=None):
    # Same triggered-Event pattern as execute_until's Horizon boundary. A normal
    # timeout can run after a completion at the same time, breaking right-open reads.
    event = simpy.Event(env)
    event._ok, event._value = True, None
    if callback is not None:
        event.callbacks.append(lambda _: callback())
    env.schedule(event, priority=priority, delay=at - env.now)
    return event


@dataclass(frozen=True)
class Params:
    engine: str = "bounded"
    policy: str = "spt"
    hard_limit: int = 12
    wip_cap: int = 4
    burst: int = 3
    interval: float = 0.5
    feedback: bool = True
    record: bool = True


class PythonPlant:
    def __init__(self, env, params):
        from simulatte.dispatching_rules import first_come_first_served, shortest_processing_time
        from simulatte.policies.conwip import ConWIP
        from simulatte.psp import PreShopPool
        from simulatte.server import Server
        from simulatte.shopfloor import ShopFloor

        self.env = env
        self.shop = ShopFloor(env=env, name="shopfloor", default_metrics=False)
        self.psp = PreShopPool(env=env, shopfloor=self.shop, name="psp")
        self.servers = [Server(env=env, shopfloor=self.shop, capacity=1) for _ in range(3)]
        ConWIP(shopfloor=self.shop, psp=self.psp, wip_cap=params.wip_cap)
        self.collector = ShopFloorKPIs(self.shop).attach(env)
        self.priority = shortest_processing_time if params.policy == "spt" else first_come_first_served

    def add(self, job):
        from simulatte.job import ProductionJob

        actor = ProductionJob(
            env=self.env,
            sku="feedback",
            servers=[self.servers[s] for s in job.route],
            processing_times=job.durations,
            due_date=job.due,
            priority_policy=self.priority,
        )
        self.env.process(self.arrive(actor))
        return actor

    def arrive(self, actor):
        self.psp.add(actor)
        if False:
            yield

    def inspect(self, actor):
        return dict(
            state="finished" if actor.done else "active" if actor.psp_exit_at is not None else "pool",
            released=actor.psp_exit_at,
        )

    def kpi_values(self):
        return {key.partition("/")[2]: value for key, value in self.collector.scalars().items()}


class Controller:
    def __init__(self, env, params):
        assert params.engine in ("python", "full", "bounded")
        assert params.policy in ("fifo", "spt") and params.hard_limit >= 4 and params.burst > 0
        self.env, self.params = env, params
        self.seed = env.rng("feedback-input").getrandbits(32)
        self.cursor = self.tick = self.completed = self.blocked = self.short = self.normal = 0
        self.tight = self.relaxed = self.pool_polls = self.expired = 0
        self.max_owned = self.max_state_bytes = 0
        self.next_at = 0.0
        self.phase = "initial"
        self.live = {}
        self.jobs_hash, self.decisions_hash = hashlib.sha256(), hashlib.sha256()
        self.writer = None
        self.proof_file = None
        self.adapter = None
        if params.record:
            self.proof_file = (env.artifacts_dir / "frames.jsonl.gz").open("wb")
            self.writer = gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=self.proof_file,
                mtime=0,
                compresslevel=1,
            )

    def state(self):
        return dict(
            schema="feedback-controller-v1",
            seed=self.seed,
            cursor=self.cursor,
            tick=self.tick,
            phase=self.phase,
            next_at=self.next_at,
            interval=self.params.interval,
            hard_limit=self.params.hard_limit,
            burst=self.params.burst,
            feedback=self.params.feedback,
            completed=self.completed,
            blocked=self.blocked,
            short=self.short,
            normal=self.normal,
            tight=self.tight,
            relaxed=self.relaxed,
            pool_polls=self.pool_polls,
            jobs_sha256=self.jobs_hash.hexdigest(),
            decisions_sha256=self.decisions_hash.hexdigest(),
            live=[dict(id=i, arrival=v[1], due=v[2]) for i, v in self.live.items()],
        )

    def observations(self):
        result = []
        for identity, (handle, arrival, due) in self.live.items():
            try:
                value = self.adapter.inspect(handle)
                state = value["state"]
            except ExpiredHandleError:
                self.expired += 1
                state, value = "finished", {}
            result.append(
                dict(
                    id=identity,
                    arrival=arrival,
                    due=due,
                    state=state,
                    released=None if state == "finished" or value.get("released") is None else float(value["released"]),
                )
            )
        return result

    def kpis(self):
        values = self.adapter.kpi_values()
        return {k.name: values.get(k.name) for k in ShopFloorKPIs.kpis}

    def poll(self):
        self.phase = "observing"
        observed, kpis = self.observations(), self.kpis()
        completed = [v["id"] for v in observed if v["state"] == "finished"]
        for identity in completed:
            del self.live[identity]
        self.completed += len(completed)
        pool = sum(v["state"] == "pool" for v in observed)
        self.pool_polls += bool(pool)
        tight = self.params.feedback and (kpis["tardy_fraction"] or 0) > 0.35
        target = self.params.hard_limit // 2 if tight else self.params.hard_limit
        self.tight += bool(tight)
        self.relaxed += not tight
        choice = "short" if self.params.feedback and pool else "normal"
        count = min(self.params.burst, max(0, target - len(self.live)))
        self.blocked += count == 0
        jobs = []
        for _ in range(count):
            job = candidate(self.seed, self.cursor, self.env.now, choice)
            handle = self.adapter.add(job)
            if self.params.engine != "python":
                assert handle.id == self.cursor
            self.live[self.cursor] = (handle, job.arrival, job.due)
            self.cursor += 1
            self.jobs_hash.update(encoded(job_wire(job)))
            jobs.append(job_wire(job))
        self.short += count if choice == "short" else 0
        self.normal += count if choice == "normal" else 0
        row = dict(
            t=float(self.env.now),
            tick=self.tick,
            observed=observed,
            kpis=kpis,
            completed=completed,
            target=target,
            choice=choice,
            jobs=jobs,
        )
        data = encoded(row)
        self.decisions_hash.update(data)
        if self.writer is not None:
            self.writer.write(data)
        self.tick += 1
        self.next_at = self.tick * self.params.interval
        self.phase = "waiting"
        self.max_owned = max(self.max_owned, len(self.live))
        self.max_state_bytes = max(self.max_state_bytes, len(encoded(self.state())))
        assert len(self.live) <= self.params.hard_limit and self.max_state_bytes < 8192
        if os.environ.get("SIMULATTE_FEEDBACK_EXTRA_QUERIES") == "1":
            before = self.state()
            self.kpis()
            if self.params.engine != "python":
                self.adapter.state_digest()
                self.adapter.storage_stats()
            assert self.state() == before

    def run(self):
        while True:
            self.poll()
            yield wake(self.env, self.next_at)


class ControllerState(Entity, kind="feedback_controller"):
    state_schema = StateSchema({"commitment": FieldSpec("str")})

    def __init__(self, env, controller):
        self.commitment = hashlib.sha256(encoded(controller.state())).hexdigest()
        env.entities.attach(self, name="feedback-controller")


@event_type("feedback.controller_committed", version=1)
@dataclass(frozen=True)
class ControllerCommitted(DomainEvent):
    commitment: str


@model(params=Params, horizon=80)
def build(env, layout, params):
    controller = Controller(env, params)

    class ObservedNative(NativeProduction):
        def close(self):
            if self._closed:
                return
            try:
                super().close()
            finally:
                directory = os.environ.get("SIMULATTE_FEEDBACK_DIAGNOSTICS")
                if directory:
                    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    if sys.platform != "darwin":
                        peak *= 1024
                    value = dict(
                        pid=os.getpid(),
                        peak_rss=peak,
                        storage=getattr(controller, "final_storage", None),
                        max_owned=controller.max_owned,
                        max_callback_bytes=controller.max_state_bytes,
                        expired_reads=controller.expired,
                        entities=len(env.entities.live()),
                    )
                    Path(directory, str(os.getpid()) + ".json").write_bytes(encoded(value))

    if params.engine == "python":
        controller.adapter = PythonPlant(env, params)
    else:
        controller.adapter = ObservedNative(
            3, wip_cap=params.wip_cap, policy=params.policy, retention=params.engine, input_state=controller.state
        )
        env.attach_execution_backend(controller.adapter, scope="shopfloor")
    entity = ControllerState(env, controller)

    def finish():
        controller.phase = "horizon"
        physical = dict(controller=controller.state(), final_observed=controller.observations(), kpis=controller.kpis())
        entity.commitment = hashlib.sha256(encoded(controller.state())).hexdigest()
        env.emit(
            ControllerCommitted(
                commitment=entity.commitment,
                deltas=Deltas.build().set(entity.id, "commitment", entity.commitment).done(),
            )
        )
        (env.artifacts_dir / "physical.json").write_bytes(encoded(physical))
        if controller.writer is not None:
            controller.writer.close()
            controller.proof_file.close()
        if params.engine != "python":
            backend = controller.adapter
            state = dict(schema=backend.capabilities.state_schema, digest=backend.state_digest())
            (env.artifacts_dir / "native.json").write_bytes(encoded(state))
            controller.final_storage = backend.storage_stats()

    env.process(controller.run())
    wake(env, env._execution_policy.until, priority=-1, callback=finish)
