"""Installed static-capacity gate; copied outside the source tree by check_install."""

import hashlib
import json
from pathlib import Path
import shutil
import sys

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.experiments import Experiment, Explicit, Replications, run
from simulatte.experimental.native import Job, NativeStream
from simulatte.stopping import Horizon

MODEL = """from dataclasses import dataclass
import hashlib, json
from simulatte import model
from simulatte.experimental.native import Job, NativeProduction
from simulatte.experimental.native_identity import compose_native_identity
@dataclass(frozen=True)
class Params:
    retention: str = "full"
    policy: str = "spt"
    dynamic: bool = False
@model(params=Params, horizon=40)
def build(env, layout, params):
    jobs = tuple(Job(i // 3 * 0.5, ((i % 3), (i + 1) % 3),
                     (0.25 * (1 + i % 4), 0.5), 3 + i % 7)
                 for i in range(48))
    cursor = [0]
    def inputs():
        return dict(next=cursor[0], total=len(jobs), seed=0)
    backend = NativeProduction(3, () if params.dynamic else jobs, capacities=(2, 1, 3),
                               wip_cap=5, policy=params.policy, retention=params.retention,
                               input_state=inputs if params.dynamic else None)
    env.attach_execution_backend(backend)
    rows = []
    def poll():
        view = backend.state_projection()
        identity = compose_native_identity(view, actions=dict(cursor=cursor[0]))
        assert identity.to_dict()["evidence"]["reproduction"]["authority"] == view["authority"]
        assert view["authority"]["digest"] == backend.state_digest()
        rows.append(dict(at=float(env.now), schema=view["authority"]["schema"],
                         codec=view["trajectory"]["codec"],
                         identity=hashlib.sha256(identity.canonical_bytes).hexdigest(),
                         storage=backend.storage_stats(), scalars=dict(backend.kpi_values())))
    def observations():
        for t in (0.25, 2, 8, 20, 39):
            yield env.timeout(t - env.now)
            poll()
        (env.artifacts_dir / "observations.json").write_text(json.dumps(rows, sort_keys=True)+"\\n")
    env.process(observations())
    if params.dynamic:
        def source():
            for i, job in enumerate(jobs):
                yield env.timeout(job.arrival - env.now)
                backend.add(job)
                cursor[0] = i + 1
        env.process(source())
"""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(output):
    assert shutil.which("cc") is None
    direct = []
    for capacities in ((1, 1), (2, 1)):
        with NativeStream(2, capacities=capacities, wip_cap=None) as stream:
            for _ in range(4):
                stream.add(Job(0, (0, 1), (2, 0.25), 10))
            stream.run()
            direct.append(
                dict(capacities=capacities, finished=stream.result()["finished"], scalars=stream.snapshot()["scalars"])
            )
    assert direct[1]["finished"][-1] < direct[0]["finished"][-1]
    project = output.parent / "capacity-model"
    project.mkdir()
    (project / "plant.py").write_text(MODEL)
    (project / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    configurations = [
        dict(
            label=f"{policy}-{retention}-{dynamic}",
            parameters=dict(policy=policy, retention=retention, dynamic=dynamic),
        )
        for policy in ("fifo", "spt")
        for retention in ("full", "bounded")
        for dynamic in (False, True)
    ]
    definition = Experiment(
        name="installed parallel native slots",
        model="plant.py:build",
        stopping=Horizon(40, warmup=2),
        level="kpi",
        configurations=Explicit(configurations),
        replications=Replications(count=1, base_seed=0),
    )
    status = run(definition, project=project, slots=SlotConfig(local=2), timeout=40)
    assert status["counts"]["succeeded"] == 8, status
    spec = discover_project(project / "plant.py", project)
    rows = []
    with Coordinator(spec) as coordinator:
        for item in coordinator.store.replications(status["run_id"]):
            result = coordinator.store.result(item["attempt"])
            trace = Trace.open(result.path / "run.simtrace")
            assert trace.fingerprint.digest == result.fingerprint["digest"]
            observations = result.path / "artifacts/observations.json"
            values = json.loads(observations.read_text())
            bounded = configurations[item["configuration"]]["parameters"]["retention"] == "bounded"
            assert all(row["codec"].startswith("NBST0002" if bounded else "NSTA0002") for row in values)
            if bounded:
                assert values[-1]["storage"]["retained_jobs"] == 0
            assert "capacity" in values[-1]["schema"]
            rows.append(
                dict(
                    configuration=item["configuration"],
                    request=result.key.request_id,
                    attempt=item["attempt"],
                    fingerprint=result.fingerprint["digest"],
                    kpis=dict(result.fingerprint["kpis"]),
                    observations_sha256=sha(observations),
                )
            )
    for policy in range(2):
        same = [row["kpis"] for row in rows if row["configuration"] // 4 == policy]
        assert len(same) == 4 and all(value == same[0] for value in same)
    (project / "plant.py").unlink()
    reproduced = []
    with Coordinator.from_store(project, rows[0]["request"]) as coordinator:
        for row in rows:
            result = coordinator.reproduce(row["request"], timeout=40)
            assert result.fingerprint["digest"] == row["fingerprint"]
            assert sha(result.path / "artifacts/observations.json") == row["observations_sha256"]
            assert result.key.attempt_id != row["attempt"]
            reproduced.append(row["request"])
    assert not any(name.startswith("prototypes") for name in sys.modules)
    output.write_text(
        json.dumps(dict(direct=direct, rows=rows, source_deleted_reproductions=reproduced), indent=2) + "\n"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
