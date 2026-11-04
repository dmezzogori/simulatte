"""Fixed workload definitions and a production-actor oracle, without native internals."""

from dataclasses import dataclass
import hashlib
import json

import simpy


@dataclass(frozen=True)
class Case:
    capacities: tuple[int, ...]
    wip_cap: int
    hard_limit: int


CASES = {
    "flow": Case((2, 2, 2), 12, 64),
    "mixed": Case((3, 1, 2, 1, 2), 9, 64),
    "burst": Case((2, 1, 3), 8, 64),
}


def specification(case, seed, index):
    code = (index + seed * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
    code ^= code >> 30
    code = code * 0xBF58476D1CE4E5B9 & ((1 << 64) - 1)
    code ^= code >> 27
    if case == "flow":
        route, durations, offered = (0, 1, 2), (0.5, 0.75, 0.5), index // 2
    elif case == "mixed":
        servers = list(range(5))
        for i in range(4, 0, -1):
            j = (code >> (i * 7)) % (i + 1)
            servers[i], servers[j] = servers[j], servers[i]
        route = tuple(servers[: 2 + code % 4])
        durations = tuple((0.1, 0.3, 0.5, 0.75, 1.2, 2.0)[(code >> (i * 4)) % 6] for i in range(len(route)))
        offered = index // 2
    else:
        routes = ((0, 1, 2), (1, 0, 2), (2, 1, 0), (0, 2, 1))
        route = routes[code % 4]
        durations = tuple((0.5, 1.5, 0.75)[server] for server in route)
        offered = index // 6 * 3
    return dict(route=route, durations=durations, due=float(offered + 8 + (code >> 19) % 12))


def wake(env, at, priority=0):
    event = simpy.Event(env)
    event._ok, event._value = True, None
    env.schedule(event, priority=priority, delay=at - env.now)
    return event


class Admission:
    def __init__(self, case, seed, jobs):
        self.case, self.seed, self.jobs = case, seed, jobs
        self.cursor = self.tick = self.blocked = self.tight = self.peak = 0
        self.actions = hashlib.sha256()

    def offer(self, outstanding):
        self.peak = max(self.peak, outstanding)
        requested = 2 if self.case != "burst" else 6 if self.tick % 3 == 0 else 0
        if self.case == "burst" and outstanding >= 32:
            requested = min(requested, 1)
            self.tight += 1
        count = min(requested, CASES[self.case].hard_limit - outstanding, self.jobs - self.cursor)
        self.blocked += requested - count
        row = [self.tick, self.cursor, outstanding, count]
        self.actions.update((json.dumps(row, separators=(",", ":")) + "\n").encode())
        self.peak = max(self.peak, outstanding + count)
        return count

    def inputs(self):
        return dict(
            schema="native-stabilization-input-v1",
            case=self.case,
            seed=self.seed,
            jobs=self.jobs,
            cursor=self.cursor,
            tick=self.tick,
            blocked=self.blocked,
            tight=self.tight,
            actions=self.actions.hexdigest(),
            hard_limit=CASES[self.case].hard_limit,
        )


class PythonPlant:
    def __init__(self, env, case, policy):
        from simulatte.collectors import ShopFloorKPIs
        from simulatte.dispatching_rules import first_come_first_served, shortest_processing_time
        from simulatte.job import ProductionJob
        from simulatte.policies.conwip import ConWIP
        from simulatte.psp import PreShopPool
        from simulatte.server import Server
        from simulatte.shopfloor import JobFinished, ShopFloor

        self.env, self.actors, self.indices, self.starts, self.dispatch = env, [], {}, [], []
        self.admitted = self.completed = 0
        self.job_type = ProductionJob
        shop = ShopFloor(env=env, name="shopfloor", default_metrics=False, on_before_operation=self.start)
        self.psp = PreShopPool(env=env, shopfloor=shop)
        config = CASES[case]
        self.servers = [Server(env=env, capacity=n, shopfloor=shop) for n in config.capacities]
        ConWIP(shopfloor=shop, psp=self.psp, wip_cap=config.wip_cap)
        self.collector = ShopFloorKPIs(shop).attach(env)
        self.priority = shortest_processing_time if policy == "spt" else first_come_first_served
        env.bus.subscribe(self.finish, (JobFinished,))

    def finish(self, event):
        self.completed += 1

    def start(self, job, server, op_index, processing_time):
        op = self.indices[job.id, server.id]
        self.starts[op] = self.env.now
        self.dispatch.append(op)

    def add(self, job):
        actor = self.job_type(
            env=self.env,
            sku="representative",
            servers=[self.servers[i] for i in job.route],
            processing_times=job.durations,
            due_date=job.due,
            priority_policy=self.priority,
        )
        offset = len(self.starts)
        self.starts.extend([None] * len(job.route))
        for i, server in enumerate(actor.routing):
            self.indices[actor.id, server.id] = offset + i
        self.actors.append(actor)
        self.admitted += 1
        self.env.process(self.arrive(actor))

    def arrive(self, actor):
        self.psp.add(actor)
        if False:
            yield

    def counts(self):
        return self.admitted, self.completed

    def kpis(self):
        return self.collector.scalars()

    def result(self):
        return dict(
            starts=self.starts,
            ends=[a.servers_exit_at[s] for a in self.actors for s in a.routing],
            released=[a.psp_exit_at for a in self.actors],
            finished=[a.finished_at for a in self.actors],
            dispatch=self.dispatch,
        )
