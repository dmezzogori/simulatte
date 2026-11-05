"""Check terminal publication and final KPIs in the installed Studio run store."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from simulatte import Trace
from simulatte.execution.schema import _plain
from simulatte.execution.store import RunStore
from simulatte.experimental.native import NativeObservation
from simulatte.stopping import stopping_from_dict


def main(args):
    summary = json.loads(args.browser.read_text())
    assert summary["seconds"] >= 900
    observed = summary["observed"]
    assert set(observed.values()) == {"succeeded", "cancelled", "failed"}
    rows = []
    with RunStore.open(args.project / ".simulatte") as store:
        with store._transaction() as db:
            indexed = {row[0] for row in db.execute("SELECT attempt FROM results")}
            attempts = {row["id"]: row for row in db.execute("SELECT * FROM attempts")}
        for identity, status in observed.items():
            attempt = attempts[identity]
            assert attempt["status"] == status
            request = store.request(attempt["request_id"])
            parameters = _plain(request.parameters)
            row = dict(attempt=identity, status=status, parameters=parameters, seed=request.seed)
            assert parameters["engine"] == "native"
            if status != "succeeded":
                assert identity not in indexed and not (store.root / "results" / identity).exists()
                assert parameters["fault"] is (status == "failed")
                if status == "cancelled":
                    assert identity in summary["cancelled"]
            else:
                result = store.result(identity)
                file = result.path / "run.simtrace"
                trace = Trace.open(file)
                trace.check()
                count = len(
                    NativeObservation(interval=0.25, max_samples=parameters["budget"]).times(
                        stopping_from_dict(_plain(request.stopping))
                    )
                )
                kpis = json.loads((result.path / "artifacts/kpis.json").read_text())
                control = json.loads((result.path / "artifacts/control.json").read_text())
                assert kpis == dict(result.fingerprint["kpis"])
                assert control["tick"] == 4096 and control["peak"] <= 64
                assert len(trace.kpi_series()) == len(kpis) == 8
                for name, points in trace.kpi_series().items():
                    assert len(points) == count and points[0][0][0] == 100 and points[-1][0][0] == 4096
                    assert points[-1][1] == kpis[name]
                with file.open("rb") as stream:
                    row["trace_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                row.update(control=control, kpis=kpis, samples=count)
            rows.append(row)
    args.output.write_text(
        json.dumps(dict(passed=True, statuses=dict(Counter(observed.values())), rows=rows), indent=2) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
