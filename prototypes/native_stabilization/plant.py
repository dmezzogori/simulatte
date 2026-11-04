"""Representative models; wall pacing is only for persistent Studio observation."""

from dataclasses import dataclass
import json
import time

from simulatte import model
from simulatte.experimental.native import Job, NativeObservation, NativeProduction
from workloads import Admission, CASES, PythonPlant, specification, wake


@dataclass(frozen=True)
class Params:
    case: str = "flow"
    engine: str = "native"
    retention: str = "bounded"
    policy: str = "spt"
    jobs: int = 16000
    budget: int = 1000
    pace: float = 0
    fault: bool = False


@model(params=Params, horizon=4096, warmup=100)
def build(env, layout, params):
    if params.case not in CASES or params.engine not in ("native", "python") or not 1 <= params.jobs <= 1_000_000:
        raise ValueError("invalid representative model configuration")
    if not 0 <= params.pace <= 0.1:
        raise ValueError("pace outside research bounds")
    horizon = env._execution_policy.until
    if int(horizon) != horizon:
        raise ValueError("representative model uses integer observation ticks")
    seed = env.rng("native-stabilization-specifications").getrandbits(32)
    control = Admission(params.case, seed, params.jobs)
    if params.engine == "native":
        case = CASES[params.case]
        backend = NativeProduction(
            len(case.capacities),
            capacities=case.capacities,
            wip_cap=case.wip_cap,
            policy=params.policy,
            retention=params.retention,
            input_state=control.inputs,
            observation=NativeObservation(interval=0.25, max_samples=params.budget) if params.budget else None,
        )
        env.attach_execution_backend(backend)

        def counts():
            values = backend.storage_stats()
            return values["admitted"], values["completed"]

        def kpis():
            return {"shopfloor/" + k: v for k, v in backend.kpi_values().items() if v is not None}

        add = backend.add
    else:
        python = PythonPlant(env, params.case, params.policy)
        counts, kpis, add = python.counts, python.kpis, python.add

    def source():
        while control.tick < horizon:
            yield wake(env, control.tick)
            admitted, completed = counts()
            for _ in range(control.offer(admitted - completed)):
                values = specification(params.case, seed, control.cursor)
                add(Job(env.now, values["route"], values["durations"], values["due"]))
                control.cursor += 1
            control.tick += 1
            if params.fault and control.tick == int(horizon / 2):
                raise ValueError("representative producer failure after native progress")
            if params.pace:
                time.sleep(params.pace)
        yield wake(env, horizon, priority=-1)
        admitted, completed = counts()
        (env.artifacts_dir / "control.json").write_text(
            json.dumps(
                control.inputs()
                | dict(admitted=admitted, completed=completed, outstanding=admitted - completed, peak=control.peak),
                sort_keys=True,
            )
            + "\n"
        )
        (env.artifacts_dir / "kpis.json").write_text(json.dumps(kpis(), sort_keys=True) + "\n")
        if params.engine == "native":
            (env.artifacts_dir / "storage.json").write_text(json.dumps(backend.storage_stats(), sort_keys=True) + "\n")

    env.process(source())
