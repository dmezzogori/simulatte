"""Standalone fixed-specification model with reporting-independent admission control."""

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


def specification(seed, index):
    """Immutable requirements/deadline, with no admission time or state argument."""
    x = (index + seed * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
    x ^= x >> 30
    x = x * 0xBF58476D1CE4E5B9 & ((1 << 64) - 1)
    x ^= x >> 27
    routes = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))
    offered = float(index // 12 * 16 + index % 12 * 0.125)
    return dict(
        id=index,
        offered=offered,
        route=routes[x % 6],
        durations=tuple((2 + (x >> (4 * k)) % 4) / 4 for k in range(3)),
        due=float(offered + 6 + (x >> 16) % 13),
    )


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
        assert params.policy in ("fifo", "spt") and params.hard_limit >= 4
        assert params.burst > 0 and params.interval > 0
        self.env, self.params = env, params
        self.seed = env.rng("fixed-admission-input").getrandbits(32)
        self.cursor = self.tick = self.completed = self.blocked = self.idle = 0
        self.tight_polls = self.relaxed_polls = self.switches = self.pool_polls = 0
        self.pressure_q = self.expired = self.delay_eighths = 0
        self.tight_mode = False
        self.max_owned = self.max_state_bytes = 0
        self.next_at, self.phase = 0.0, "initial"
        self.live = {}
        self.specs_hash, self.admissions_hash = hashlib.sha256(), hashlib.sha256()
        self.decisions_hash, self.reports_hash = hashlib.sha256(), hashlib.sha256()
        self.adapter = None
        self.writer = self.report_writer = None
        self.proof_file = self.report_file = None
        if params.record:
            self.proof_file = (env.artifacts_dir / "controls.jsonl.gz").open("wb")
            self.report_file = (env.artifacts_dir / "reports.jsonl.gz").open("wb")
            self.writer = gzip.GzipFile(filename="", mode="wb", fileobj=self.proof_file, mtime=0, compresslevel=1)
            self.report_writer = gzip.GzipFile(
                filename="", mode="wb", fileobj=self.report_file, mtime=0, compresslevel=1
            )

    def state(self):
        # Reporting values, reporting hashes and reporting windows are deliberately
        # absent: they do not determine future admissions or control observations.
        return dict(
            schema="fixed-admission-controller-v1",
            seed=self.seed,
            cursor=self.cursor,
            tick=self.tick,
            phase=self.phase,
            next_at=self.next_at,
            interval=self.params.interval,
            hard_limit=self.params.hard_limit,
            burst=self.params.burst,
            feedback=self.params.feedback,
            pressure_q=self.pressure_q,
            tight_mode=self.tight_mode,
            completed=self.completed,
            blocked=self.blocked,
            idle=self.idle,
            tight_polls=self.tight_polls,
            relaxed_polls=self.relaxed_polls,
            switches=self.switches,
            pool_polls=self.pool_polls,
            delay_eighths=self.delay_eighths,
            specifications_sha256=self.specs_hash.hexdigest(),
            admissions_sha256=self.admissions_hash.hexdigest(),
            decisions_sha256=self.decisions_hash.hexdigest(),
            live=[dict(id=i, arrival=v[1], offered=v[2], due=v[3]) for i, v in self.live.items()],
        )

    def observations(self):
        result = []
        for identity, (handle, arrival, offered, due) in self.live.items():
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
                    offered=offered,
                    due=due,
                    state=state,
                    released=None if state == "finished" or value.get("released") is None else float(value["released"]),
                )
            )
        return result

    def report_values(self):
        values = self.adapter.kpi_values()
        return {k.name: values.get(k.name) for k in ShopFloorKPIs.kpis}

    def poll(self):
        self.phase = "observing"
        observed = self.observations()
        report = self.report_values() if self.report_writer is not None else None
        completed = [o["id"] for o in observed if o["state"] == "finished"]
        for identity in completed:
            del self.live[identity]
        self.completed += len(completed)
        pool = sum(o["state"] == "pool" for o in observed)
        self.pool_polls += pool > 0
        instant_q = 256 * pool // len(self.live) if self.live else 0
        self.pressure_q = (self.pressure_q + instant_q) // 2
        previous = self.tight_mode
        if not self.params.feedback:
            self.tight_mode = False
        elif self.pressure_q >= 64:
            self.tight_mode = True
        elif self.pressure_q <= 16:
            self.tight_mode = False
        self.switches += self.tight_mode != previous
        self.tight_polls += self.tight_mode
        self.relaxed_polls += not self.tight_mode
        target = self.params.hard_limit // 2 if self.tight_mode else self.params.hard_limit
        credit = max(0, target - len(self.live))
        ready = specification(self.seed, self.cursor)["offered"] <= self.env.now
        self.blocked += ready and credit == 0
        self.idle += not ready
        jobs = []
        for _ in range(min(self.params.burst, credit)):
            spec = specification(self.seed, self.cursor)
            if spec["offered"] > self.env.now:
                break
            job = Job(float(self.env.now), spec["route"], spec["durations"], spec["due"])
            handle = self.adapter.add(job)
            if self.params.engine != "python":
                assert handle.id == self.cursor
            self.live[self.cursor] = (handle, job.arrival, spec["offered"], job.due)
            record = dict(spec, arrival=job.arrival)
            self.specs_hash.update(encoded(spec))
            self.admissions_hash.update(encoded(record))
            self.delay_eighths += int(round((job.arrival - spec["offered"]) * 8))
            self.cursor += 1
            jobs.append(record)
        row = dict(
            t=float(self.env.now),
            tick=self.tick,
            observed=observed,
            completed=completed,
            pressure_q=self.pressure_q,
            tight=self.tight_mode,
            target=target,
            credit=credit,
            ready=ready,
            jobs=jobs,
        )
        data = encoded(row)
        self.decisions_hash.update(data)
        if self.writer is not None:
            self.writer.write(data)
            reports = encoded(dict(t=float(self.env.now), tick=self.tick, kpis=report))
            self.reports_hash.update(reports)
            self.report_writer.write(reports)
        self.tick += 1
        self.next_at = self.tick * self.params.interval
        self.phase = "waiting"
        self.max_owned = max(self.max_owned, len(self.live))
        self.max_state_bytes = max(self.max_state_bytes, len(encoded(self.state())))
        assert len(self.live) <= self.params.hard_limit and self.max_state_bytes < 8192
        assert 0 <= self.pressure_q <= 256
        if os.environ.get("SIMULATTE_FIXED_EXTRA_QUERIES") == "1":
            before = self.state()
            self.report_values()
            if self.params.engine != "python":
                self.adapter.state_digest()
                self.adapter.storage_stats()
            assert self.state() == before

    def run(self):
        while True:
            self.poll()
            yield wake(self.env, self.next_at)


class ControllerState(Entity, kind="fixed_admission_controller"):
    state_schema = StateSchema({"commitment": FieldSpec("str")})

    def __init__(self, env, controller):
        self.commitment = hashlib.sha256(encoded(controller.state())).hexdigest()
        env.entities.attach(self, name="admission-controller")


@event_type("fixed_admission.controller_committed", version=1)
@dataclass(frozen=True)
class ControllerCommitted(DomainEvent):
    commitment: str


@model(params=Params, horizon=96)
def build(env, layout, params):
    controller = Controller(env, params)

    class ObservedNative(NativeProduction):
        def close(self):
            if self._closed:
                return
            try:
                super().close()
            finally:
                directory = os.environ.get("SIMULATTE_FIXED_DIAGNOSTICS")
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
        control = dict(controller=controller.state(), final_observed=controller.observations())
        reporting = dict(kpis=controller.report_values(), reports_sha256=controller.reports_hash.hexdigest())
        entity.commitment = hashlib.sha256(encoded(controller.state())).hexdigest()
        env.emit(
            ControllerCommitted(
                commitment=entity.commitment,
                deltas=Deltas.build().set(entity.id, "commitment", entity.commitment).done(),
            )
        )
        (env.artifacts_dir / "control.json").write_bytes(encoded(control))
        (env.artifacts_dir / "reporting.json").write_bytes(encoded(reporting))
        if controller.writer is not None:
            controller.writer.close()
            controller.proof_file.close()
            controller.report_writer.close()
            controller.report_file.close()
        if params.engine != "python":
            backend = controller.adapter
            state = dict(schema=backend.capabilities.state_schema, digest=backend.state_digest())
            (env.artifacts_dir / "native.json").write_bytes(encoded(state))
            controller.final_storage = backend.storage_stats()

    env.process(controller.run())
    wake(env, env._execution_policy.until, priority=-1, callback=finish)
