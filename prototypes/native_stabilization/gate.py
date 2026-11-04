"""Installed representative model matrix with production-actor KPI/control oracle."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.schema import _plain
from simulatte.execution.snapshot import discover_project
from simulatte.experimental.native import NativeObservation
from simulatte.stopping import Horizon


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows, baselines, authorities, selected = [], {}, {}, []
    spec = discover_project(args.project / "plant.py", args.project)
    with Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=30)
        for case in ("flow", "mixed", "burst"):
            for policy in ("fifo", "spt"):
                for seed in (3, 21):
                    for warmup in (0, 64):
                        group = (case, policy, seed, warmup)
                        variants = [("python", "full", 0)] + [
                            ("native", mode, budget) for mode in ("full", "bounded") for budget in (0, 257, 4096)
                        ]
                        for engine, retention, budget in variants:
                            params = dict(
                                case=case,
                                policy=policy,
                                engine=engine,
                                retention=retention,
                                budget=budget,
                                jobs=600,
                                pace=0,
                                fault=False,
                            )
                            request = coordinator.make_request(
                                prepared,
                                parameters=params,
                                seed=seed,
                                stopping=Horizon(256, warmup=warmup).to_dict(),
                                level="kpi",
                            )
                            result = coordinator.wait(
                                coordinator.submit(request, prepared.revision, timeout=30), timeout=40
                            )
                            control = json.loads((result.path / "artifacts/control.json").read_text())
                            kpis = json.loads((result.path / "artifacts/kpis.json").read_text())
                            assert kpis == dict(result.fingerprint["kpis"])
                            assert control["peak"] <= 64 and control["admitted"] >= control["completed"] > 0
                            if engine == "python":
                                baselines[group] = (control, kpis)
                            else:
                                assert (control, kpis) == baselines[group], (
                                    params,
                                    seed,
                                    warmup,
                                    control,
                                    kpis,
                                    baselines[group],
                                )
                                key = group + (retention,)
                                actual = _plain(result.fingerprint)
                                if key not in authorities:
                                    authorities[key] = actual
                                assert actual == authorities[key], (params, "cadence changed fingerprint")
                            trace = Trace.open(result.path / "run.simtrace")
                            expected = (
                                len(
                                    NativeObservation(interval=0.25, max_samples=budget).times(
                                        Horizon(256, warmup=warmup)
                                    )
                                )
                                if budget
                                else 0
                            )
                            series = trace.kpi_series()
                            if expected:
                                assert len(series) == 8 and all(len(points) == expected for points in series.values())
                            else:
                                assert not series
                            row = dict(
                                parameters=params,
                                seed=seed,
                                warmup=warmup,
                                request=result.key.request_id,
                                key=asdict(result.key),
                                control=control,
                                kpis=kpis,
                                fingerprint=_plain(result.fingerprint),
                                samples=expected,
                                trace_sha256=hashlib.sha256((result.path / "run.simtrace").read_bytes()).hexdigest(),
                            )
                            rows.append(row)
                            if engine == "native" and budget == 257 and seed == 3 and warmup == 64:
                                selected.append(row)
                        print(
                            json.dumps(
                                dict(
                                    groups=len(baselines),
                                    attempts=len(rows),
                                    case=case,
                                    policy=policy,
                                    seed=seed,
                                    warmup=warmup,
                                )
                            ),
                            flush=True,
                        )
    # The admission trajectory cannot depend on reporting warmup.
    for case in ("flow", "mixed", "burst"):
        for policy in ("fifo", "spt"):
            for seed in (3, 21):
                assert baselines[case, policy, seed, 0][0] == baselines[case, policy, seed, 64][0]
    for name in ("plant.py", "workloads.py"):
        (args.project / name).unlink()
    reproduced = []
    with Coordinator.from_store(args.project, selected[0]["request"]) as coordinator:
        for original in selected:
            result = coordinator.reproduce(original["request"], timeout=40)
            assert _plain(result.fingerprint) == original["fingerprint"]
            assert json.loads((result.path / "artifacts/control.json").read_text()) == original["control"]
            assert json.loads((result.path / "artifacts/kpis.json").read_text()) == original["kpis"]
            assert result.key.attempt_id != original["key"]["attempt_id"]
            reproduced.append(asdict(result.key))
    (output / "matrix.json").write_text(
        json.dumps(dict(passed=True, rows=rows, reproductions=reproduced), indent=2) + "\n"
    )
    print(json.dumps(dict(passed=True, requests=len(rows), reproduced=len(reproduced))), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
