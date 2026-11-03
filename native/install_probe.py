"""Run only from an installed wheel environment, outside the repository."""

import json
from importlib import metadata
from pathlib import Path
import shutil
import sys

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.experiments import Experiment, Explicit, Replications, run
from simulatte.experimental.native import Job, NativeStream
from simulatte.stopping import Horizon
import simulatte_native

MODEL = """from dataclasses import dataclass
import importlib
from pathlib import Path
from simulatte import model
from simulatte.experimental.native import Job, NativeProduction
@dataclass(frozen=True)
class Params:
    dynamic: bool = False
    policy: str = "spt"
    corrupt: bool = False
@model(params=Params, horizon=20)
def build(env, layout, params):
    rng = env.rng("native-example")
    jobs = tuple(Job(i * 0.25, tuple(rng.sample(range(3), 2)),
                     (rng.uniform(0.5, 1.5), rng.uniform(0.5, 1.5)), 10 + i)
                 for i in range(24))
    cursor = [0]
    def input_state():
        return dict(next=cursor[0], future=[(j.arrival,j.route,j.durations,j.due) for j in jobs])
    backend = NativeProduction(3, () if params.dynamic else jobs,
                               wip_cap=2, policy=params.policy,
                               input_state=input_state if params.dynamic else None)
    env.attach_execution_backend(backend)
    if params.corrupt:
        provider = importlib.import_module("simulatte_native")
        extension = next((Path(provider.__file__).parent / "_bin").glob("_nd_scalar.*"))
        with extension.open("ab") as file:
            file.write(b"runtime-corruption-probe")
    if params.dynamic:
        def arrivals():
            for index, job in enumerate(jobs):
                yield env.timeout(job.arrival - env.now)
                backend.add(job)
                cursor[0] = index + 1
        env.process(arrivals())
"""


def main(output, *, corrupt=False):
    assert shutil.which("cc") is None and shutil.which("clang") is None
    with NativeStream(2, wip_cap=2) as stream:
        stream.add(Job(0, (0, 1), (2, 3), 10))
        stream.add(Job(1, (1, 0), (1, 2), 10))
        stream.run()
        direct = stream.snapshot()
        assert direct["scalars"]["shopfloor/utilization"] == 0.8
        assert direct["scalars"]["shopfloor/throughput"] == 0.4
    project = output.parent / "model"
    project.mkdir()
    source_only = project / "native-source/src/simulatte_native"
    source_only.mkdir(parents=True)
    (source_only / "__init__.py").write_text("raise AssertionError('noneditable source shadowed installed wheel')\n")
    (project / "plant.py").write_text(MODEL)
    (project / "pyproject.toml").write_text(
        '[tool.simulatte.bundle]\ninclude=["plant.py"]\n'
        '[tool.uv.sources]\nsimulatte-native={path="native-source",editable=false}\n'
    )
    if corrupt:
        spec = discover_project(project / "plant.py", project)
        with Coordinator(spec) as coordinator:
            prepared = coordinator.prepare(timeout=30)
            request = coordinator.make_request(prepared, parameters={"corrupt": True}, level="kpi")
            try:
                coordinator.wait(coordinator.submit(request, prepared.revision, timeout=30), timeout=40)
            except ExecutionFailure as error:
                failure = str(error)
            else:
                raise AssertionError("mutated installed binary published a result")
            with coordinator.store._transaction() as db:
                assert db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
        output.write_text(json.dumps(dict(rejected=True, error=failure)) + "\n")
        return
    definition = Experiment(
        name="installed native provider",
        model="plant.py:build",
        stopping=Horizon(10, warmup=2),
        level="kpi",
        configurations=Explicit(
            [
                dict(label=f"{policy}-{dynamic}", parameters=dict(policy=policy, dynamic=dynamic))
                for policy in ("fifo", "spt")
                for dynamic in (False, True)
            ]
        ),
        replications=Replications(count=2, base_seed=100),
    )
    status = run(definition, project=project, slots=SlotConfig(local=2), timeout=40)
    assert status["counts"]["succeeded"] == 8 and status["counts"].get("reused", 0) == 0
    spec = discover_project(project / "plant.py", project)
    rows, selected = [], []
    with Coordinator(spec) as coordinator:
        for item in coordinator.store.replications(status["run_id"]):
            result = coordinator.store.result(item["attempt"])
            trace = Trace.open(result.path / "run.simtrace")
            assert trace.fingerprint.digest == result.fingerprint["digest"]
            imports = result.manifest["resolved"]["imports"]
            origin = imports["simulatte_native._nd_scalar"]
            assert origin.startswith("external:") and "site-packages/simulatte_native/_bin/" in origin
            assert not any(name.startswith("prototypes") for name in imports)
            row = dict(
                configuration=item["configuration"],
                seed=item["seed"],
                fingerprint=result.fingerprint["digest"],
                kpis=dict(result.fingerprint["kpis"]),
                attempt=item["attempt"],
                origin=origin,
                request=result.key.request_id,
            )
            rows.append(row)
            if item["index"] == 0:
                selected.append(row)
        assert len({r["attempt"] for r in rows}) == 8
        for policy in range(2):
            for seed in ("100", "101"):
                pair = [r for r in rows if r["configuration"] // 2 == policy and str(r["seed"]) == seed]
                assert len(pair) == 2 and pair[0]["kpis"] == pair[1]["kpis"]
    (project / "plant.py").unlink()
    reproduced = []
    with Coordinator.from_store(project, selected[0]["request"]) as coordinator:
        for original in selected:
            repeated = coordinator.reproduce(original["request"], timeout=40)
            assert repeated.fingerprint["digest"] == original["fingerprint"]
            assert repeated.key.attempt_id != original["attempt"]
            reproduced.append(original["request"])
    assert not any(name.startswith("prototypes") for name in sys.modules)
    output.write_text(
        json.dumps(
            dict(
                direct=direct,
                rows=rows,
                source_deleted_reproductions=reproduced,
                provider=simulatte_native.info(),
                distributions={d.metadata["Name"]: d.version for d in metadata.distributions()},
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), corrupt="--corrupt" in sys.argv)
