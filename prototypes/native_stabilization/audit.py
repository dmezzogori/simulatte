"""Audit the timed Studio attempts, then reproduce stored requests without source."""

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.schema import _plain
from simulatte.execution.store import RunStore
from simulatte.experimental.native import NativeObservation
from simulatte.stopping import stopping_from_dict


def main(args):
    browser = json.loads(args.browser.read_text())
    # Publication/reproduction is audited independently of the browser error gate.
    assert browser["seconds"] >= 900
    observed = browser["observed"]
    assert {"succeeded", "cancelled", "failed"} <= set(observed.values())
    rows, selected = [], {}
    with RunStore.open(args.project / ".simulatte") as store:
        with store._transaction() as db:
            attempts = db.execute("SELECT * FROM attempts ORDER BY generation").fetchall()
            published = {row[0] for row in db.execute("SELECT attempt FROM results")}
        for attempt in attempts:
            identity = attempt["id"]
            if identity not in observed:
                continue
            assert attempt["status"] == observed[identity]
            request = store.request(attempt["request_id"])
            row = dict(
                attempt=identity,
                request=attempt["request_id"],
                generation=attempt["generation"],
                status=attempt["status"],
                parameters=_plain(request.parameters),
                seed=request.seed,
            )
            if attempt["status"] != "succeeded":
                assert identity not in published
                assert not (store.root / "results" / identity).exists()
                if attempt["status"] == "failed":
                    assert request.parameters["fault"] is True
                else:
                    assert identity in browser["cancelled"]
            else:
                result = store.result(identity)
                trace = Trace.open(result.path / "run.simtrace")
                trace.check()
                assert trace.verify() == "not_verifiable"  # KPI traces carry no entity event replay.
                control = json.loads((result.path / "artifacts/control.json").read_text())
                kpis = json.loads((result.path / "artifacts/kpis.json").read_text())
                storage = json.loads((result.path / "artifacts/storage.json").read_text())
                if request.parameters["retention"] == "bounded":
                    assert storage["retained_jobs"] == storage["admitted"] - storage["completed"] <= 64
                    assert storage["forgotten"] == storage["completed"]
                    assert storage["retained_operations"] <= 320 and storage["job_capacity"] <= 128
                else:
                    assert storage["retained_jobs"] == storage["admitted"] and storage["forgotten"] == 0
                assert kpis == dict(result.fingerprint["kpis"])
                assert control["tick"] == 4096 and control["peak"] <= 64
                assert control["admitted"] >= control["completed"] > 1000
                count = len(
                    NativeObservation(interval=0.25, max_samples=request.parameters["budget"]).times(
                        stopping_from_dict(_plain(request.stopping))
                    )
                )
                series = trace.kpi_series()
                assert len(series) == 8 and all(len(points) == count for points in series.values())
                for name, points in series.items():
                    assert points[0][0][0] == 100 and points[-1][0][0] == 4096
                    assert points[-1][1] == kpis[name]
                assert result.manifest["simulation"]["complete"]
                assert not result.manifest["resolved"]["warnings"]
                row.update(
                    control=control,
                    kpis=kpis,
                    storage=storage,
                    samples=count,
                    fingerprint=_plain(result.fingerprint),
                    trace_sha256=hashlib.sha256((result.path / "run.simtrace").read_bytes()).hexdigest(),
                )
                selected[request.parameters["case"], request.parameters["retention"]] = row
            rows.append(row)
    assert len(rows) == len(observed)
    assert len(selected) == 6
    assert {row["parameters"]["policy"] for row in rows} == {"fifo", "spt"}
    for name in ("plant.py", "workloads.py"):
        (args.project / name).unlink()
    reproduced = []
    with Coordinator.from_store(args.project, next(iter(selected.values()))["request"]) as coordinator:
        for original in selected.values():
            result = coordinator.reproduce(original["request"], timeout=180)
            assert result.key.attempt_id != original["attempt"]
            assert _plain(result.fingerprint) == original["fingerprint"]
            for name in ("control", "kpis", "storage"):
                assert json.loads((result.path / f"artifacts/{name}.json").read_text()) == original[name]
            reproduced.append(dict(original=original["attempt"], key=asdict(result.key)))
            print(
                json.dumps(
                    dict(
                        reproduced=len(reproduced),
                        case=original["parameters"]["case"],
                        retention=original["parameters"]["retention"],
                    )
                ),
                flush=True,
            )
    args.output.write_text(
        json.dumps(
            dict(
                passed=True,
                statuses=dict(Counter(row["status"] for row in rows)),
                rows=rows,
                reproductions=reproduced,
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
