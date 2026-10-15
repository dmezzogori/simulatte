"""Validated input and policy boundary shared by the prototype backends."""

from __future__ import annotations

import math
import operator
from dataclasses import dataclass


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class Job:
    arrival: float
    route: tuple[int, ...]
    durations: tuple[float, ...]
    due: float

    def __post_init__(self):
        arrival = finite(self.arrival, "arrival")
        due = finite(self.due, "due")
        route = tuple(self.route)
        durations = tuple(finite(d, "duration") for d in self.durations)
        if arrival < 0 or any(d <= 0 for d in durations):
            raise ValueError("arrival must be nonnegative and durations positive")
        if not route or len(route) != len(durations) or len(set(route)) != len(route):
            raise ValueError("route must be nonempty, unique and match durations")
        if any(isinstance(s, bool) or not isinstance(s, int) or s < 0 for s in route):
            raise ValueError("server indices must be nonnegative integers")
        object.__setattr__(self, "arrival", arrival)
        object.__setattr__(self, "due", due)
        object.__setattr__(self, "route", route)
        object.__setattr__(self, "durations", durations)


@dataclass(frozen=True, slots=True)
class Workload:
    servers: int
    jobs: tuple[Job, ...]

    def __post_init__(self):
        if isinstance(self.servers, bool) or not isinstance(self.servers, int) or not 0 < self.servers < 2**32 - 1:
            raise ValueError("servers must be a positive uint32 count")
        jobs = tuple(self.jobs)
        if any(not isinstance(j, Job) for j in jobs):
            raise TypeError("jobs must contain Job instances")
        if any(s >= self.servers for j in jobs for s in j.route):
            raise ValueError("server index outside workload")
        if any(a.arrival > b.arrival for a, b in zip(jobs, jobs[1:])):
            raise ValueError("arrivals must be nondecreasing")
        if len(jobs) >= 2**32 - 1 or sum(len(j.route) for j in jobs) >= 2**32 - 1:
            raise ValueError("prototype IDs must fit uint32")
        object.__setattr__(self, "jobs", jobs)

    @property
    def operation_count(self):
        return sum(len(j.route) for j in self.jobs)


def flatten(workload):
    offsets, servers, durations, owners = [0], [], [], []
    for job_id, job in enumerate(workload.jobs):
        servers.extend(job.route)
        durations.extend(job.durations)
        owners.extend([job_id] * len(job.route))
        offsets.append(len(servers))
    return offsets, servers, durations, owners


def fifo_hook(now, server, candidates):
    return candidates[0][0]


def spt_hook(now, server, candidates):
    return min(candidates, key=lambda candidate: candidate[1])[0]


def choose_hook(hook, now, server, candidates):
    answer = hook(now, server, candidates)
    if isinstance(answer, bool):
        raise TypeError("policy returned bool instead of operation ID")
    try:
        answer = operator.index(answer)
    except TypeError:
        raise TypeError("policy must return an integer operation ID") from None
    if not any(op == answer for op, _ in candidates):
        raise ValueError("policy returned an operation outside its candidates")
    return answer


def check_options(policy, mode, hook, trace_path):
    if policy not in ("fifo", "spt") or mode not in ("none", "kpi", "record"):
        raise ValueError("invalid policy or observation mode")
    if hook is not None and not callable(hook):
        raise TypeError("hook must be callable")
    if (mode == "record") != (trace_path is not None):
        raise ValueError("trace_path is required exactly in record mode")
