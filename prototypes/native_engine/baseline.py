"""Actual current Simulatte flow, with prototype observations and optional batch policy."""

from __future__ import annotations

import heapq
import math

from simpy.events import NORMAL

from simulatte import Environment
from simulatte.dispatching_rules import first_come_first_served, shortest_processing_time
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

from .model import check_options, choose_hook, flatten
from .recording import Observations


class _ObservedJob(ProductionJob):
    __slots__ = ("prototype_id", "prototype_op", "prototype_enqueue")


class _ObservedServer(Server):
    def request(self, *, job, preempt=True):
        runner = self.prototype_runner
        job.prototype_enqueue = runner.enqueue_sequence
        runner.enqueue_sequence += 1
        op = runner.offsets[job.prototype_id] + job.prototype_op
        runner.observations.queue(self.env.now, job.prototype_id, op, self.prototype_id)
        return super().request(job=job, preempt=preempt)

    def sort_queue(self):
        runner = self.prototype_runner
        if runner.hook is None:
            return super().sort_queue()
        if self.count >= self.capacity or not self.queue:
            return
        waiting = sorted(self.queue, key=lambda request: request.job.prototype_enqueue)
        candidates = tuple(
            (runner.offsets[r.job.prototype_id] + r.job.prototype_op, r.job.routing[self]) for r in waiting
        )
        runner.callbacks += 1
        selected = choose_hook(runner.hook, self.env.now, self.prototype_id, candidates)
        winner = next(r for r, (op, _) in zip(waiting, candidates) if op == selected)
        self.queue.remove(winner)
        self.queue.insert(0, winner)


class _ObservedShopFloor(ShopFloor):
    def _operate(self, job, server, op_index, processing_time):
        yield from super()._operate(job, server, op_index, processing_time)
        runner = self.prototype_runner
        runner.observations.end(
            self.env.now,
            job.prototype_id,
            runner.offsets[job.prototype_id] + op_index,
            server.prototype_id,
            processing_time,
        )
        job.prototype_op += 1


class SimulatteRunner:
    def __init__(self, workload, *, policy="fifo", mode="none", hook=None, trace_path=None):
        check_options(policy, mode, hook, trace_path)
        self.workload, self.hook = workload, hook
        self.offsets, _, _, _ = flatten(workload)
        self.callbacks = self.events = self.enqueue_sequence = 0
        self.state = "built"
        self.priority = first_come_first_served if policy == "fifo" else shortest_processing_time
        self.observations = Observations(workload, mode, trace_path)
        self.env = Environment(seed=0)
        self.shop = _ObservedShopFloor(
            env=self.env, default_metrics=False, on_before_operation=self._start, on_job_finished=self._finish
        )
        self.shop.prototype_runner = self
        self.servers = []
        for index in range(workload.servers):
            server = _ObservedServer(env=self.env, capacity=1, shopfloor=self.shop)
            server.prototype_runner, server.prototype_id = self, index
            self.servers.append(server)
        self.env.process(self._feed())

    def _feed(self):
        for index, source in enumerate(self.workload.jobs):
            if source.arrival > self.env.now:
                # Input timestamps are absolute binary64 values. now + (at - now)
                # can differ from at by one ulp, even splitting an equal-time batch.
                # Use the normal SimPy event priority and insertion counter directly;
                # resource/process scheduling remains the actual current runtime.
                arrival = self.env.event()
                arrival._ok, arrival._value = True, None
                heapq.heappush(self.env._queue, (source.arrival, NORMAL, next(self.env._eid), arrival))
                yield arrival
            self.observations.arrival(self.env.now, index)
            job = _ObservedJob(
                env=self.env,
                sku="prototype",
                servers=[self.servers[s] for s in source.route],
                processing_times=source.durations,
                due_date=source.due,
                priority_policy=self.priority,
            )
            job.prototype_id, job.prototype_op = index, 0
            self.shop.add(job)

    def _start(self, job, server, op_index, processing_time):
        at = self.env.now + processing_time
        if not math.isfinite(at) or at <= self.env.now:
            raise ValueError("completion time is nonfinite or does not advance")
        self.observations.start(
            self.env.now, job.prototype_id, self.offsets[job.prototype_id] + op_index, server.prototype_id
        )

    def _finish(self, job):
        self.observations.finish(self.env.now, job.prototype_id)

    def run(self):
        if self.state != "built":
            raise RuntimeError("runner can only run once before close")
        self.state = "running"
        try:
            self.env.run()
            # Drained calendar: all scheduled events were processed; no per-step instrumentation.
            self.events = next(self.env._eid)
            self.observations.flush()
            self.state = "done"
        except BaseException:
            self.state = "failed"
            raise

    def result(self):
        if self.state != "done":
            raise RuntimeError("result requires a successful run before close")
        return self.observations.result(self.events, self.callbacks)

    def close(self):
        if self.state == "running":
            raise RuntimeError("cannot close a running runner")
        if self.state != "closed":
            try:
                self.observations.close()
            finally:
                self.env.close()
                self.state = "closed"
