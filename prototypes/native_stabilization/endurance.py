"""Longer-horizon installed production-actor parity, alongside the Studio soak."""

import argparse
import json
from pathlib import Path
import shutil

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.schema import _plain
from simulatte.execution.snapshot import discover_project
from simulatte.stopping import Horizon


def main(args):
    args.project.mkdir(exist_ok=False)
    for name in ("plant.py", "workloads.py", "pyproject.toml"):
        shutil.copyfile(args.source / name, args.project / name)
    rows = []
    with Coordinator(discover_project(args.project / "plant.py", args.project)) as coordinator:
        prepared = coordinator.prepare(timeout=30)
        for case in ("flow", "mixed", "burst"):
            for policy in ("fifo", "spt"):
                oracle = None
                for engine, retention in (("python", "full"), ("native", "full"), ("native", "bounded")):
                    parameters = dict(
                        case=case,
                        policy=policy,
                        engine=engine,
                        retention=retention,
                        budget=4096 if engine == "native" else 0,
                        jobs=40000,
                        pace=0,
                        fault=False,
                    )
                    request = coordinator.make_request(
                        prepared,
                        parameters=parameters,
                        seed=77,
                        stopping=Horizon(16384, warmup=100).to_dict(),
                        level="kpi",
                    )
                    result = coordinator.wait(coordinator.submit(request, prepared.revision, timeout=180), timeout=190)
                    control = json.loads((result.path / "artifacts/control.json").read_text())
                    kpis = json.loads((result.path / "artifacts/kpis.json").read_text())
                    assert control["tick"] == 16384 and control["peak"] <= 64
                    assert control["completed"] > 10000
                    if oracle is None:
                        oracle = (control, kpis)
                    else:
                        assert (control, kpis) == oracle, (case, policy, retention)
                    storage = (
                        json.loads((result.path / "artifacts/storage.json").read_text()) if engine == "native" else None
                    )
                    if retention == "bounded":
                        assert storage["forgotten"] == control["completed"]
                        assert storage["retained_jobs"] == control["outstanding"] <= 64
                        assert storage["job_capacity"] <= 128
                    rows.append(
                        dict(
                            parameters=parameters,
                            control=control,
                            kpis=kpis,
                            storage=storage,
                            request=result.key.request_id,
                            fingerprint=_plain(result.fingerprint),
                        )
                    )
                    print(
                        json.dumps(
                            dict(
                                case=case,
                                policy=policy,
                                engine=engine,
                                retention=retention,
                                admitted=control["admitted"],
                                completed=control["completed"],
                            )
                        ),
                        flush=True,
                    )
    args.output.write_text(json.dumps(dict(passed=True, rows=rows), indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
