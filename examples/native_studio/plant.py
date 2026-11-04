"""Experimental packaged native execution with bounded Studio KPI observation.

From this directory, in an environment with the matching native and Studio extras:
    simulatte studio plant.py --level kpi
"""

from dataclasses import dataclass

from simulatte import model
from simulatte.experimental.native import Job, NativeObservation, NativeProduction


@dataclass(frozen=True)
class Params:
    jobs: int = 1200
    retention: str = "bounded"
    parallel_slots: int = 2
    observation_interval: float = 10
    observation_limit: int = 1000


@model(params=Params, horizon=1000, warmup=100)
def build(env, layout, params):
    if not 1 <= params.jobs <= 100_000 or not 1 <= params.parallel_slots <= 16:
        raise ValueError("Require 1..100000 jobs and 1..16 parallel slots")
    seed = env.rng("native-studio-input").getrandbits(32)
    cursor, next_tick = 0, 0.0

    def inputs():
        return dict(schema="native-studio-input-v1", seed=seed, cursor=cursor, jobs=params.jobs, next_tick=next_tick)

    backend = NativeProduction(
        3,
        capacities=(params.parallel_slots, 1, 2),
        wip_cap=8,
        retention=params.retention,
        input_state=inputs,
        observation=NativeObservation(interval=params.observation_interval, max_samples=params.observation_limit),
    )
    env.attach_execution_backend(backend)

    def admissions():
        nonlocal cursor, next_tick
        while cursor < params.jobs:
            yield env.timeout(next_tick - env.now)
            counts = backend.storage_stats()
            if counts["admitted"] - counts["completed"] < 32:
                code = (seed + cursor * 2654435761) & 0xFFFFFFFF
                first = code % 3
                backend.add(Job(env.now, (first, (first + 1) % 3), (0.25 * (1 + (code >> 4) % 6), 0.5), env.now + 12))
                cursor += 1
            next_tick += 0.5

    env.process(admissions())
