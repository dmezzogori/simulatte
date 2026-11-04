"""Installed native observations, invariant results and source-deleted reproduction."""

import json
import math
from pathlib import Path
import shutil
import sys

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.schema import _plain
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.experiments import Experiment, Explicit, Replications, run
from simulatte.stopping import Horizon

MODEL = """from dataclasses import dataclass
from simulatte import model
from simulatte.experimental.native import Job, NativeObservation, NativeProduction
@dataclass(frozen=True)
class Params:
    retention: str = "bounded"
    policy: str = "spt"
    dynamic: bool = False
    observation: str = "off"
@model(params=Params, horizon=40, warmup=2)
def build(env, layout, params):
    cursor = [0]
    jobs = tuple(Job(i // 3 * 0.5, (i % 3, (i + 1) % 3), (0.25 * (1 + i % 4), 0.5), 3 + i % 7)
                 for i in range(48))
    def inputs():
        return dict(cursor=cursor[0], count=len(jobs))
    observation = None if params.observation == "off" else NativeObservation(
        interval=0.25, max_samples=17 if params.observation == "budgeted" else 1000)
    backend = NativeProduction(3, () if params.dynamic else jobs, capacities=(2,1,3),
                               retention=params.retention, policy=params.policy, wip_cap=5,
                               input_state=inputs if params.dynamic else None, observation=observation)
    env.attach_execution_backend(backend)
    if params.dynamic:
        def source():
            for i, job in enumerate(jobs):
                yield env.timeout(job.arrival - env.now)
                backend.add(job)
                cursor[0] = i + 1
        env.process(source())
"""


def main(output):
    assert shutil.which("cc") is None
    project = output.parent / "observation-model"
    project.mkdir()
    (project / "plant.py").write_text(MODEL)
    (project / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    configurations = [
        dict(
            label=f"{retention}-{policy}-{dynamic}-{observation}",
            parameters=dict(retention=retention, policy=policy, dynamic=dynamic, observation=observation),
        )
        for retention in ("full", "bounded")
        for policy in ("fifo", "spt")
        for dynamic in (False, True)
        for observation in ("off", "fine", "budgeted")
    ]
    definition = Experiment(
        name="installed native observation",
        model="plant.py:build",
        level="kpi",
        stopping=Horizon(40, warmup=2),
        configurations=Explicit(configurations),
        replications=Replications(count=1, base_seed=0),
    )
    status = run(definition, project=project, slots=SlotConfig(local=2), timeout=40)
    assert status["counts"]["succeeded"] == 24, status
    spec = discover_project(project / "plant.py", project)
    rows, selected, fixtures = [], [], set()
    with Coordinator(spec) as coordinator:
        for item in coordinator.store.replications(status["run_id"]):
            result = coordinator.store.result(item["attempt"])
            trace = Trace.open(result.path / "run.simtrace")
            assert trace.fingerprint.digest == result.fingerprint["digest"]
            config = configurations[item["configuration"]]["parameters"]
            capabilities = trace.execution["initial"]["resolved"]["capabilities"]["execution_backend"]
            assert capabilities["experimental"] is True
            series = trace.kpi_series()
            expected = {"off": 0, "fine": 153, "budgeted": 17}[config["observation"]]
            if expected:
                assert len(series) == 8 and all(len(points) == expected for points in series.values())
                assert all(points[0][0][0] == 2 and points[-1][0][0] == 40 for points in series.values())
                for key, points in series.items():
                    assert points[-1][1] == trace.kpis()[key]
                    assert math.isnan(points[0][1])
                assert capabilities["observation"]["scheduled_samples"] == expected
            else:
                assert not series and "observation" not in capabilities
            row = dict(
                configuration=item["configuration"],
                parameters=config,
                request=result.key.request_id,
                attempt=item["attempt"],
                fingerprint=_plain(result.fingerprint),
                samples_per_kpi=expected,
            )
            rows.append(row)
            if config["observation"] == "budgeted":
                selected.append(row)
                if config["retention"] not in fixtures:
                    shutil.copyfile(
                        result.path / "run.simtrace", output.parent / f"observation-{config['retention']}.simtrace"
                    )
                    fixtures.add(config["retention"])
        for group in range(8):
            same = [row["fingerprint"] for row in rows if row["configuration"] // 3 == group]
            assert len(same) == 3 and all(value == same[0] for value in same)
    (project / "plant.py").unlink()
    reproduced = []
    with Coordinator.from_store(project, selected[0]["request"]) as coordinator:
        for row in selected:
            result = coordinator.reproduce(row["request"], timeout=40)
            assert _plain(result.fingerprint) == row["fingerprint"] and result.key.attempt_id != row["attempt"]
            assert all(len(points) == 17 for points in Trace.open(result.path / "run.simtrace").kpi_series().values())
            reproduced.append(row["request"])
    assert not any(name.startswith("prototypes") for name in sys.modules)
    output.write_text(json.dumps(dict(rows=rows, source_deleted_reproductions=reproduced), indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
