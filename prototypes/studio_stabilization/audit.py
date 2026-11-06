"""Check terminal publication and final KPIs in the installed Studio run store."""

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from simulatte import Trace
from simulatte.execution.schema import _plain
from simulatte.execution.store import RunStore, _read
from simulatte.experimental.native import NativeObservation
from simulatte.stopping import stopping_from_dict

EXPECTED = {"succeeded", "cancelled", "failed"}


async def stopped_revocations(store, observed):
    """Exercise the installed HTTP route without starting the Studio lifecycle."""
    import httpx

    from simulatte.studio.app import StudioServer
    from simulatte.studio.server import assets

    server = StudioServer(SimpleNamespace(store=store), 18762, assets(), watcher=object())
    headers = {
        "X-Simulatte-Session": server.secret,
        "Cookie": f"{server.cookie_name}={server.cookie}",
    }
    rows = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url=server.origin, headers=headers
    ) as client:
        success = next((attempt for attempt, status in observed.items() if status == "succeeded"), None)
        if success is not None:
            with store._transaction() as db:
                generation = db.execute(
                    "SELECT MAX(sequence) FROM receipts WHERE attempt=? AND stream='trace'", (success,)
                ).fetchone()[0]
            response = await client.get(f"/api/prefix/{success}/{generation}", headers={"Range": "bytes=0-0"})
            assert response.status_code == 206 and len(response.content) == 1
        for attempt, status in observed.items():
            if status != "succeeded":
                response = await client.get(f"/api/prefix/{attempt}/1", headers={"Range": "bytes=0-0"})
                assert response.status_code == 410 and response.json()["error"] == "Provisional trace was invalidated"
                rows.append(dict(attempt=attempt, status=status, http=response.status_code))
    return dict(passed=True, mode="stopped_store_asgi", rows=rows)


def main(args):
    summary = json.loads(args.browser.read_text())
    assert summary["seconds"] >= 900
    observed = summary["observed"]
    rows = []
    unexpected = []
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
                if (
                    status not in EXPECTED
                    or parameters["fault"] is not (status == "failed")
                    or (status == "cancelled" and identity not in summary["cancelled"])
                ):
                    row["details"] = _read(attempt["details"])
                    unexpected.append(row)
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
        if args.stopped_revocations:
            revocations = asyncio.run(stopped_revocations(store, observed))
            args.stopped_revocations.write_text(json.dumps(revocations, indent=2) + "\n")
    args.output.write_text(
        json.dumps(
            dict(
                passed=True,
                expected_outcomes=set(observed.values()) == EXPECTED and not unexpected,
                statuses=dict(Counter(observed.values())),
                unexpected=unexpected,
                rows=rows,
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
    parser.add_argument("--stopped-revocations", type=Path)
    main(parser.parse_args())
