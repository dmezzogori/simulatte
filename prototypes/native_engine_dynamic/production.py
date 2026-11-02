"""Research adapter over unmodified production Simulatte/SimPy actors.

The two operation hooks export the trajectory returned by the native API. They
do not select jobs, schedule operations, or implement the release policy.
"""

import math

from simulatte import Environment, TraceRecorder
from simulatte.dispatching_rules import first_come_first_served, shortest_processing_time
from simulatte.job import ProductionJob
from simulatte.policies.conwip import ConWIP
from simulatte.psp import PreShopPool, PspEntered
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor, ShopFloorEntered, JobFinished
from simulatte.trace import ChunkLimits

from .handles import JobHandle
from .studio_trace import ProjectionKPIs, SCALARS


class ProductionKPIs(ProjectionKPIs):
    subscribes = (PspEntered, ShopFloorEntered, JobFinished)

    def __init__(self, scope):
        super().__init__(scope)
        self.system = self.floor = 0

    def on_activate(self):
        self.sample("system_wip", 0)
        self.sample("floor_wip", 0)

    def on_event(self, event):
        if isinstance(event, PspEntered):
            self.system += 1
            self.sample("system_wip", self.system)
        elif isinstance(event, ShopFloorEntered):
            self.floor += 1
            self.sample("floor_wip", self.floor)
        else:
            self.system -= 1
            self.floor -= 1
            self.sample("system_wip", self.system)
            self.sample("floor_wip", self.floor)


def totals(jobs, offsets, starts, ends, released, finished, servers):
    """Derived export of the production run; no control decisions use it."""
    flow = math.fsum(t - j.arrival for j, t in zip(jobs, finished))
    pool = math.fsum(t - j.arrival for j, t in zip(jobs, released))
    waits, busy = [], [0.0] * servers
    for i, job in enumerate(jobs):
        for local, (server, duration) in enumerate(zip(job.route, job.durations)):
            op = offsets[i] + local
            queued = released[i] if local == 0 else ends[op - 1]
            waits.append(starts[op] - queued)
            busy[server] += duration

    def peak(arrivals, *, departures_first=False):
        level = maximum = 0
        for _, _, delta in sorted(
            [(t, int(departures_first), 1) for t in arrivals] + [(t, int(not departures_first), -1) for t in finished]
        ):
            level += delta
            maximum = max(maximum, level)
        return maximum

    return dict(
        total_flow=flow,
        total_wait=math.fsum(waits),
        total_tardiness=math.fsum(max(0, t - j.due) for j, t in zip(jobs, finished)),
        wip_area=flow,
        max_wip=peak([j.arrival for j in jobs]),
        total_pool_wait=pool,
        floor_wip_area=math.fsum(t - r for t, r in zip(finished, released)),
        max_floor_wip=peak(released, departures_first=True),
        busy=busy,
    )


class ProductionStream:
    kpi_type = ProductionKPIs
    always_export_totals = False

    def configure_observers(self):
        pass

    def __init__(self, servers, *, wip_cap=12, policy="spt", mode="none", trace_path=None, provenance=None):
        if mode not in ("none", "kpi", "full") or policy not in ("spt", "fifo"):
            raise ValueError("invalid production mode or policy")
        self.servers_count, self.mode = servers, mode
        self.jobs, self.actors, self.arrived = [], [], []
        self.offsets, self.starts, self.ends, self.dispatch = [0], [], [], []
        self.identities = {}
        self.owner = object()
        self.closed = self.ran = False
        self.env = Environment(seed=0, time_unit="time", provenance=provenance)
        self.recorder = (
            TraceRecorder(
                self.env,
                trace_path,
                level="full" if mode == "full" else "kpi",
                chunk_limits=ChunkLimits(max_latency_s=float("inf")),
            )
            if mode != "none"
            else None
        )
        self.shop = ShopFloor(
            env=self.env,
            name="shopfloor",
            default_metrics=False,
            on_before_operation=self._start,
            on_after_operation=self._end,
        )
        self.psp = PreShopPool(env=self.env, shopfloor=self.shop, name="psp")
        self.servers = [Server(env=self.env, capacity=1, shopfloor=self.shop) for _ in range(servers)]
        self.policy = ConWIP(shopfloor=self.shop, psp=self.psp, wip_cap=wip_cap)
        self.priority = shortest_processing_time if policy == "spt" else first_come_first_served
        self.kpis = self.kpi_type(self.shop).attach(self.env) if mode != "none" else None
        self.configure_observers()
        self.env.activate()

    @property
    def now(self):
        return self.env.now

    def _start(self, job, server, op_index, processing_time):
        op = self.offsets[self.identities[job]] + op_index
        self.starts[op] = self.now
        self.dispatch.append(op)

    def _end(self, job, server, op_index, processing_time):
        self.ends[self.offsets[self.identities[job]] + op_index] = self.now

    def _arrival(self, identity):
        self.arrived[identity] = True
        self.psp.add(self.actors[identity])
        # An ordinary SimPy input process, with no extra simulated delay.
        if False:
            yield

    def add(self, source):
        if source.arrival != self.now:
            raise ValueError("production comparison registers at arrival")
        identity = len(self.jobs)
        job = ProductionJob(
            env=self.env,
            sku="native",
            servers=[self.servers[s] for s in source.route],
            processing_times=source.durations,
            due_date=source.due,
            priority_policy=self.priority,
        )
        self.jobs.append(source)
        self.actors.append(job)
        self.arrived.append(False)
        self.identities[job] = identity
        self.offsets.append(self.offsets[-1] + len(source.route))
        self.starts.extend([0.0] * len(source.route))
        self.ends.extend([0.0] * len(source.route))
        self.env.process(self._arrival(identity))
        return JobHandle(self.owner, identity)

    def advance(self, until):
        if until < self.now:
            raise ValueError("cannot advance backwards")
        if until > self.now:
            self.env.run(until=until)

    def inspect(self, handle):
        if handle._owner is not self.owner:
            raise ValueError("foreign handle")
        job = self.actors[handle.id]
        state = (
            "scheduled"
            if not self.arrived[handle.id]
            else "finished"
            if job.done
            else "active"
            if job.psp_exit_at is not None
            else "pool"
        )
        return dict(
            id=handle.id, arrival=job.created_at, released=job.psp_exit_at, finished=job.finished_at, state=state
        )

    def run(self):
        self.env.run()
        self.ran = True

    def result(self):
        if not self.ran:
            raise RuntimeError("run required")
        released = [j.psp_exit_at for j in self.actors]
        finished = [j.finished_at for j in self.actors]
        kpis = (
            totals(self.jobs, self.offsets, self.starts, self.ends, released, finished, self.servers_count)
            if self.mode != "none" or self.always_export_totals
            else None
        )
        result = dict(
            starts=self.starts[:],
            ends=self.ends[:],
            released=released,
            finished=finished,
            dispatch=self.dispatch[:],
            completed=len(self.shop.jobs_done),
            makespan=max(finished, default=0),
            callbacks=0,
            events=next(self.env._eid),
            records=0,
            kpis=kpis,
        )
        if self.kpis is not None:
            self.kpis.final = {
                name: float(result[name] if name in ("completed", "makespan") else kpis[name]) for name in SCALARS
            }
        return result

    def close(self):
        if not self.closed:
            self.closed = True
            self.env.close()
