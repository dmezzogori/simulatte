"""Studio analysis commands and bounded authenticated readers over saved analysis outputs."""

import asyncio
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from simulatte.analysis import codec
from simulatte.analysis.datasets import DatasetManifest, DatasetReader
from simulatte.analysis.execution import AnalysisRunner
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore
from simulatte.execution.store import RunStore
from simulatte.projects import StudioTarget
from simulatte.projects.environments import EnvironmentPool
from simulatte.projects.service import ProjectService
from tests.core.test_experiment_results import build
from tests.execution.test_analysis_snapshots import publish

PYPROJECT = """[project]
name = "study"
version = "0.1.0"
[tool.simulatte.project]
schema = 1
id = "c09b902b-ccad-4d13-887b-693356b32f23"
[tool.simulatte.analyses.report]
id = "e64ca951-1b69-47a0-b1c5-7cfb91d90755"
name = "Report"
entrypoint = "report.py:analyze"
[tool.simulatte.analyses.slow]
id = "5d0f5d43-5b55-4a5c-8f3d-1f6f2b4f7a10"
name = "Slow"
entrypoint = "report.py:slow"
[tool.simulatte.analyses.needs]
id = "9a1e2f0c-7c1d-4b8e-9a52-3c0c3f0e6d21"
name = "Needs jobs"
entrypoint = "report.py:needs"
[tool.simulatte.analyses.required]
id = "3b7c3f0e-52a6-4c43-8d0a-1d5f9a2c7b44"
name = "Required"
entrypoint = "report.py:required"
[tool.simulatte.bundle]
include = ["report.py"]
"""

REPORT = """import math
from pathlib import Path
import tempfile
import time
from simulatte.analysis import analysis, DatasetRequirement
from simulatte.analysis.outputs import Figure, Image, Number, Table
from simulatte.analysis.types import DatasetSchema, Field
from simulatte.model import ParameterField, ParameterSchema

SCHEMA = DatasetSchema("values", (Field("index", "int64"), Field("big", "int64"), Field("value", "float64")))
SPECIAL = {1: math.nan, 2: math.inf, 3: -math.inf}


@analysis(parameters=ParameterSchema(()))
def analyze(context, parameters):
    context.emit("total", Number(sum(row["scalars"]["lateness"] for row in context.scalars()), unit="h"))
    rows = ({"index": i, "big": 2**62 + i, "value": SPECIAL.get(i, i / 2)} for i in range(10000))
    context.emit("values", Table(SCHEMA, rows))
    folder = Path(tempfile.mkdtemp())
    (folder / "plot.png").write_bytes(b"\\x89PNG\\r\\n\\x1a\\n" + b"0" * 32)
    (folder / "plot.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    context.emit("png", Image(folder / "plot.png", "image/png"))
    context.emit("svg", Image(folder / "plot.svg", "image/svg+xml"))
    figure = {"data": [{"type": "bar", "y": [1, 2]}], "layout": {}}
    context.emit("figure", Figure("plotly", figure, renderer={"name": "plotly.js", "version": "4.1.1"}))


@analysis(parameters=ParameterSchema(()))
def slow(context, parameters):
    while True:
        time.sleep(0.01)


@analysis(parameters=ParameterSchema((ParameterField("threshold", "float", True),)))
def required(context, parameters):
    pass


@analysis(
    parameters=ParameterSchema(()),
    requires=(DatasetRequirement("jobs", ("lateness",), require_complete=True),),
)
def needs(context, parameters):
    pass
"""

TERMINAL = ("succeeded", "failed", "cancelled", "lost")


def write_project(root):
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(PYPROJECT)
    (root / "report.py").write_text(REPORT)
    with RunStore.open(root / ".simulatte") as store:
        store.create_revision({})
        run, _ = build(store, {(0, i): "pending" for i in range(2)})
        attempt = publish(store, run, 0, 0, 3)
    return run, attempt


def static_dir(path):
    path.mkdir()
    (path / "index.html").write_text("<main>Studio</main>")
    (path / "assets").mkdir()
    (path / "assets/plotly-frame.html").write_text("<script src='plotly.js'></script>")
    assets = {"index.html": "unused", "assets/plotly-frame.html": "unused"}
    (path / "manifest.json").write_text(json.dumps({"assets": assets}))
    return path


def call(server, kind, **body):
    """Through the server's error mapping, like a websocket command."""
    return asyncio.run(server.command({"v": 1, "type": kind, "id": "t", "body": body}, None))


def selection(run, **changes):
    return {
        "run_ids": [],
        "experiment_run_ids": [run],
        "configurations": None,
        "require_complete": False,
        "min_replications": 0,
        **changes,
    }


def admit(server, key, run, **extra):
    preview = call(server, "analysis.preview", registration_key=key, selection=selection(run), parameters={})
    assert preview["ok"], preview
    return call(
        server,
        "analysis.admit",
        preview_id=preview["preview_id"],
        definition_id=preview["definition_id"],
        generation_id=preview["generation_id"],
        parameters={},
        **extra,
    )


def wait_state(server, identity, states, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        state = call(server, "analysis.get", execution_id=identity)["execution"]["state"]
        if state in states or time.monotonic() > deadline:
            return state
        time.sleep(0.05)


@pytest.fixture(scope="module")
def studio(tmp_path_factory):
    from simulatte.studio.project_commands import ProjectServer

    base = tmp_path_factory.mktemp("studio-analysis")
    root = base / "project"
    run, attempt = write_project(root)
    service = ProjectService.open(StudioTarget(root, None))
    generation = service.environments.use_existing(Path(sys.executable))
    service.environments.activate(generation.id)
    server = ProjectServer(service, 8123, static_dir(base / "static"))
    with TestClient(server.app, base_url=server.origin) as client:
        client.get(server.link())
        admitted = admit(server, "report", run, name="Weekly report")
        assert admitted["ok"], admitted
        identity = admitted["execution"]["id"]
        assert service.analysis_runner.wait(identity, timeout=60).state == "succeeded"
        yield SimpleNamespace(
            root=root,
            run=run,
            attempt=attempt,
            service=service,
            server=server,
            client=client,
            headers={"X-Simulatte-Session": server.secret},
            id=identity,
        )


def outputs(studio, identity=None):
    manifest = codec.from_json(call(studio.server, "analysis.get", execution_id=identity or studio.id)["output"])
    return {item["name"]: item for item in manifest["outputs"]}


def test_state_lists_registrations(studio):
    analyses = studio.service.state()["analyses"]
    assert analyses == [
        {
            "key": "report",
            "id": "e64ca951-1b69-47a0-b1c5-7cfb91d90755",
            "name": "Report",
            "entrypoint": "report.py:analyze",
        },
        {"key": "slow", "id": "5d0f5d43-5b55-4a5c-8f3d-1f6f2b4f7a10", "name": "Slow", "entrypoint": "report.py:slow"},
        {
            "key": "needs",
            "id": "9a1e2f0c-7c1d-4b8e-9a52-3c0c3f0e6d21",
            "name": "Needs jobs",
            "entrypoint": "report.py:needs",
        },
        {
            "key": "required",
            "id": "3b7c3f0e-52a6-4c43-8d0a-1d5f9a2c7b44",
            "name": "Required",
            "entrypoint": "report.py:required",
        },
    ]
    assert studio.server.snapshot()["project"]["analyses"] == analyses


def test_admitted_execution_get_shows_manifest_snapshot_and_definition(studio):
    reply = call(studio.server, "analysis.get", execution_id=studio.id)
    assert reply["ok"]
    assert set(reply) == {"ok", "execution", "definition", "snapshot", "details", "output", "annotations"}
    execution = reply["execution"]
    assert execution["state"] == "succeeded" and execution["name"] == "Weekly report"
    assert execution["parameters"] == {"$record": {}}
    assert set(reply["definition"]) == {"id", "name", "entrypoint", "registration_key", "parameters", "requirements"}
    assert reply["definition"]["registration_key"] == "report" and reply["definition"]["name"] == "Report"
    assert reply["definition"]["id"] == execution["definition_id"]
    snapshot = reply["snapshot"]
    assert (snapshot["available"], snapshot["planned"], snapshot["missing"], snapshot["partial"]) == (1, 2, 1, True)
    assert snapshot["hash"] == execution["snapshot_hash"]
    assert snapshot["selection"]["experiment_run_ids"] == [studio.run]
    assert snapshot["windows"]["total"] == 1 and snapshot["windows"]["items"][0]["replications"] == 1
    assert codec.from_json(reply["details"])["name"] == "Weekly report"
    manifest = codec.from_json(reply["output"])
    assert manifest["schema"] == "analysis-output-v1" and manifest["execution_id"] == studio.id
    assert {o["name"]: o["kind"] for o in manifest["outputs"]} == {
        "total": "number",
        "values": "table",
        "png": "image",
        "svg": "image",
        "figure": "figure",
    }
    assert reply["annotations"] == []
    with pytest.raises(KeyError):
        studio.server._command({"type": "analysis.get", "body": {"execution_id": "missing"}}, None)
    assert call(studio.server, "analysis.get", execution_id="missing")["error"]["code"] == "failed"


def test_list_pages_are_capped_and_stable(studio):
    rerun = call(studio.server, "analysis.rerun", execution_id=studio.id)
    assert rerun["ok"] and rerun["execution"]["state"] in ("waiting", "running", "finalizing", "succeeded")
    assert (
        rerun["execution"]["snapshot_hash"]
        == call(studio.server, "analysis.get", execution_id=studio.id)["execution"]["snapshot_hash"]
    )
    assert wait_state(studio.server, rerun["execution"]["id"], TERMINAL) == "succeeded"
    first = call(studio.server, "analysis.list", limit=1)
    assert first["ok"] and len(first["items"]) == 1 and first["next_cursor"]
    item = first["items"][0]
    assert item["id"] == rerun["execution"]["id"] and item["name"] == "Weekly report"
    assert set(item) == {
        "id",
        "definition_id",
        "name",
        "registration_key",
        "state",
        "created",
        "snapshot_hash",
        "available",
        "planned",
        "partial",
        "experiment_run_ids",
    }
    assert (item["available"], item["planned"], item["partial"]) == (1, 2, True)
    selection = call(studio.server, "analysis.get", execution_id=studio.id)["snapshot"]["selection"]
    assert item["experiment_run_ids"] == selection["experiment_run_ids"]
    # New admissions never shift an existing cursor.
    newer = call(studio.server, "analysis.rerun", execution_id=studio.id)
    assert wait_state(studio.server, newer["execution"]["id"], TERMINAL) == "succeeded"
    second = call(studio.server, "analysis.list", cursor=first["next_cursor"], limit=1)
    assert [i["id"] for i in second["items"]] != [newer["execution"]["id"]]
    everything = call(studio.server, "analysis.list", limit=100)
    assert len(everything["items"]) >= 3 and everything["next_cursor"] is None
    assert everything["items"][0]["id"] == newer["execution"]["id"]
    assert call(studio.server, "analysis.list")["items"] == everything["items"]
    for body in ({"limit": 0}, {"limit": 101}, {"limit": "5"}, {"cursor": "x"}, {"cursor": "01"}, {"cursor": 5}):
        reply = call(studio.server, "analysis.list", **body)
        message = reply["error"]["message"]
        assert not reply["ok"] and ("limit" in message or "cursor" in message)


def test_preview_with_insufficient_input_returns_details(studio):
    reply = call(
        studio.server, "analysis.preview", registration_key="needs", selection=selection(studio.run), parameters={}
    )
    assert reply["ok"] is False
    assert reply["error"]["code"] == "insufficient_input"
    assert "insufficient input for dataset jobs" in reply["error"]["message"]
    details = codec.from_json(reply["error"]["details"])
    assert details["dataset"] == "jobs" and details["require_complete"] is True
    assert details["valid"] == 0 and details["replications"] == 2
    # Generic completeness refusals carry the snapshot counts.
    complete = call(
        studio.server,
        "analysis.preview",
        registration_key="report",
        selection=selection(studio.run, require_complete=True),
        parameters={},
    )
    assert complete["error"]["code"] == "insufficient_input"
    assert codec.from_json(complete["error"]["details"]) == {
        "available": 1,
        "planned": 2,
        "partial": True,
        "require_complete": True,
        "min_replications": 0,
    }
    unknown = call(studio.server, "analysis.preview", registration_key="absent", selection=selection(studio.run))
    assert unknown["error"]["code"] == "failed" and "Unknown analysis registration" in unknown["error"]["message"]
    bad = call(
        studio.server,
        "analysis.preview",
        registration_key="report",
        selection={**selection(studio.run), "experiment_run_ids": studio.run},
        parameters={},
    )
    assert bad["error"]["code"] == "failed" and "list" in bad["error"]["message"]
    wrong = call(
        studio.server,
        "analysis.preview",
        registration_key="report",
        selection=selection(studio.run),
        parameters={"unknown": 1},
    )
    assert wrong["error"]["code"] == "failed"
    listed = call(
        studio.server,
        "analysis.preview",
        registration_key="report",
        selection=selection(studio.run),
        parameters=[],
    )
    assert listed["error"]["code"] == "failed" and "object" in listed["error"]["message"]


def test_preview_admit_insufficient_after_change_and_name_validation(studio, monkeypatch):
    preview = call(
        studio.server, "analysis.preview", registration_key="report", selection=selection(studio.run), parameters={}
    )
    keys = {"preview_id", "snapshot_hash", "available", "planned", "missing", "partial", "definition_id"}
    assert set(preview) == {"ok", "generation_id", "definition", *keys}
    # The parameter form is available before admission, in the same shape analysis.get uses.
    definition = preview["definition"]
    assert definition == call(studio.server, "analysis.get", execution_id=studio.id)["definition"]
    assert set(definition) == {"id", "name", "entrypoint", "registration_key", "parameters", "requirements"}
    assert definition["id"] == preview["definition_id"] and definition["registration_key"] == "report"
    assert definition["name"] == "Report" and definition["entrypoint"] == "report.py:analyze"
    assert definition["parameters"] == {"$record": {"version": 1, "fields": []}}
    assert (preview["available"], preview["planned"], preview["missing"], preview["partial"]) == (1, 2, 1, True)
    body = {k: preview[k] for k in ("preview_id", "definition_id", "generation_id")}
    assert call(studio.server, "analysis.admit", parameters={}, name="", **body)["error"]["code"] == "failed"
    from simulatte.analysis.store import InsufficientInput

    def refused(*args, **kwargs):
        raise InsufficientInput("insufficient input: changed", {"n": 2**60})

    monkeypatch.setattr(AnalysisStore, "admit", refused)
    reply = call(studio.server, "analysis.admit", parameters={}, **body)
    assert reply == {
        "ok": False,
        "error": {
            "code": "insufficient_input",
            "message": "insufficient input: changed",
            "details": {"$record": {"n": {"$int64": str(2**60)}}},
        },
    }


def test_preview_skips_parameter_validation_until_admission(studio):
    preview = call(studio.server, "analysis.preview", registration_key="required", selection=selection(studio.run))
    assert preview["ok"], preview
    assert (preview["available"], preview["planned"]) == (1, 2)
    fields = preview["definition"]["parameters"]["$record"]["fields"]
    assert [(f["$record"]["name"], f["$record"]["required"]) for f in fields] == [("threshold", True)]
    empty = call(
        studio.server, "analysis.preview", registration_key="required", selection=selection(studio.run), parameters={}
    )
    assert empty["ok"] and empty["definition"] == preview["definition"]
    # Input rules still apply without parameters: insufficient input is reported, not the schema.
    short = call(
        studio.server,
        "analysis.preview",
        registration_key="required",
        selection=selection(studio.run, require_complete=True),
    )
    assert short["ok"] is False and short["error"]["code"] == "insufficient_input"
    assert codec.from_json(short["error"]["details"])["require_complete"] is True
    # A supplied parameter object is still validated.
    wrong = call(
        studio.server,
        "analysis.preview",
        registration_key="required",
        selection=selection(studio.run),
        parameters={"threshold": "high"},
    )
    assert wrong["error"]["code"] == "failed"
    body = {k: preview[k] for k in ("preview_id", "definition_id", "generation_id")}
    refused = call(studio.server, "analysis.admit", **body)
    assert refused["error"]["code"] == "failed" and "threshold" in refused["error"]["message"]
    admitted = call(studio.server, "analysis.admit", parameters={"threshold": 0.5}, **body)
    assert admitted["ok"], admitted
    assert admitted["execution"]["parameters"] != {}


def test_rerun_keeps_snapshot_and_refresh_returns_new_preview(studio):
    store = studio.service.store
    before = call(studio.server, "analysis.get", execution_id=studio.id)["execution"]
    refreshed = call(studio.server, "analysis.refresh", execution_id=studio.id)
    assert refreshed["ok"] and "execution" not in refreshed
    assert refreshed["definition"] == call(studio.server, "analysis.get", execution_id=studio.id)["definition"]
    assert refreshed["snapshot_hash"] == before["snapshot_hash"]
    store.set_run_status(studio.run, "running")
    publish(store, studio.run, 0, 1, 5)
    refreshed = call(studio.server, "analysis.refresh", execution_id=studio.id)
    assert refreshed["snapshot_hash"] != before["snapshot_hash"]
    assert (refreshed["available"], refreshed["planned"], refreshed["missing"], refreshed["partial"]) == (
        2,
        2,
        0,
        False,
    )
    assert refreshed["definition_id"] == before["definition_id"]
    assert refreshed["generation_id"] == before["generation_id"]
    rerun = call(studio.server, "analysis.rerun", execution_id=studio.id)
    assert rerun["execution"]["snapshot_hash"] == before["snapshot_hash"]
    assert wait_state(studio.server, rerun["execution"]["id"], TERMINAL) == "succeeded"
    fresh = call(
        studio.server,
        "analysis.admit",
        preview_id=refreshed["preview_id"],
        definition_id=refreshed["definition_id"],
        generation_id=refreshed["generation_id"],
        parameters={},
    )
    assert fresh["execution"]["snapshot_hash"] == refreshed["snapshot_hash"]
    assert wait_state(studio.server, fresh["execution"]["id"], TERMINAL) == "succeeded"
    reopened = call(studio.server, "analysis.get", execution_id=fresh["execution"]["id"])
    assert reopened["execution"]["name"] == "Report"
    assert codec.from_json(reopened["output"])["outputs"][0]["metadata"]["available"] == 2
    old = call(studio.server, "analysis.get", execution_id=studio.id)
    assert codec.from_json(old["output"])["outputs"][0]["metadata"]["available"] == 1
    assert call(studio.server, "analysis.refresh", execution_id="missing")["error"]["code"] == "failed"


def test_cancel_running_analysis_and_outputs_stay_hidden(studio):
    started = admit(studio.server, "slow", studio.run)
    identity = started["execution"]["id"]
    assert wait_state(studio.server, identity, ("running",)) == "running"
    cancelled = call(studio.server, "analysis.cancel", execution_id=identity)
    assert cancelled["ok"] and cancelled["execution"]["state"] == "cancelled"
    assert wait_state(studio.server, identity, TERMINAL) == "cancelled"
    reply = call(studio.server, "analysis.get", execution_id=identity)
    assert reply["output"] is None and reply["execution"]["name"] == "Slow"
    response = studio.client.get(f"/api/analysis/{identity}/artifacts/total/number.json", headers=studio.headers)
    assert response.status_code == 409
    assert response.json()["state"] == "cancelled" and isinstance(codec.from_json(response.json()["details"]), dict)
    rows = studio.client.get(f"/api/analysis/{identity}/outputs/{'a' * 64}/rows", headers=studio.headers)
    assert rows.status_code == 409


def test_annotations_are_append_only_revisions(studio):
    first = call(studio.server, "analysis.annotate", execution_id=studio.id, text="first look")
    second = call(studio.server, "analysis.annotate", execution_id=studio.id, text="checked")
    assert first["ok"] and second["ok"] and first["revision"] != second["revision"]
    annotations = call(studio.server, "analysis.get", execution_id=studio.id)["annotations"]
    assert [a["text"] for a in annotations] == ["first look", "checked"]
    assert [a["revision"] for a in annotations] == [first["revision"], second["revision"]]
    assert all(isinstance(a["ts"], float) for a in annotations)
    items = call(studio.server, "analysis.list", limit=100)["items"]
    assert next(i for i in items if i["id"] == studio.id)["annotation"] == "checked"
    assert call(studio.server, "analysis.annotate", execution_id=studio.id, text=1)["error"]["code"] == "failed"


def test_analysis_state_notifications_follow_admission(studio):
    origin = studio.server.origin
    with studio.client.websocket_connect(origin.replace("http:", "ws:") + "/ws", headers={"Origin": origin}) as socket:
        socket.send_json({"v": 1, "type": "auth", "secret": studio.server.secret})

        def response(identity, body, kind):
            socket.send_json({"v": 1, "type": kind, "id": identity, "revision": None, "body": body})
            while True:
                message = socket.receive_json()
                if message["type"] == "response" and message["id"] == identity:
                    return message["body"]

        preview = response(
            "p1",
            {"registration_key": "report", "selection": selection(studio.run), "parameters": {}},
            "analysis.preview",
        )
        assert preview["ok"], preview
        admitted = response(
            "a1",
            {k: preview[k] for k in ("preview_id", "definition_id", "generation_id")} | {"parameters": {}},
            "analysis.admit",
        )
        identity = admitted["execution"]["id"]
        states = []
        while not states or states[-1] not in TERMINAL:
            message = socket.receive_json()
            if message["type"] == "analysis_state" and message["body"]["execution_id"] == identity:
                states.append(message["body"]["state"])
        order = ["waiting", "running", "finalizing", "succeeded"]
        assert states[-1] == "succeeded"
        assert states == sorted(set(states), key=order.index)
    assert identity not in studio.server.analysis._watched


def test_pump_forgets_deleted_and_runner_owned_executions(studio):
    commands = studio.server.analysis
    commands.watch("deleted-execution", None)
    assert commands.changes() == [] and "deleted-execution" not in commands._watched
    runner = studio.service.analyses()
    with runner._lock:
        runner._futures["owned"] = None
    try:
        assert commands.watching()
        commands.changes()
    finally:
        with runner._lock:
            runner._futures.pop("owned")
    assert "owned" not in commands._watched
    with runner._lock:
        runner._futures["owned"] = None
    try:
        # A finished execution whose future lingers is not announced again.
        assert commands.changes() == [] and "owned" not in commands._watched
    finally:
        with runner._lock:
            runner._futures.pop("owned")
    for index in range(300):
        commands.watch(f"gone-{index}", None)
    assert commands.changes() == [] and len(commands._finished) == 256


def rows_url(studio, output, **query):
    from urllib.parse import urlencode

    suffix = ("?" + urlencode(query)) if query else ""
    return f"/api/analysis/{studio.id}/outputs/{output['id']}/rows{suffix}"


def test_rows_route_pages_10000_rows_with_tagged_values(studio):
    table = outputs(studio)["values"]
    seen, cursor, pages = [], None, 0
    while True:
        query = {"limit": 500} if cursor is None else {"limit": 500, "cursor": cursor}
        response = studio.client.get(rows_url(studio, table, **query), headers=studio.headers)
        assert response.status_code == 200, response.text
        assert len(response.content) <= 1 << 20
        assert response.headers["content-type"] == "application/json"
        body = response.json()
        assert set(body) == {"rows", "next_cursor", "row_count", "indexed"}
        assert body["row_count"] == 10000 and body["indexed"] is False
        assert len(body["rows"]) <= 500
        seen.extend(body["rows"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 20 and len(seen) == 10000
    assert seen[0] == {"$record": {"index": 0, "big": {"$int64": str(2**62)}, "value": 0.0}}
    assert [row["$record"]["value"] for row in seen[1:4]] == [
        {"$float64": "nan"},
        {"$float64": "inf"},
        {"$float64": "-inf"},
    ]
    assert seen[-1]["$record"]["big"] == {"$int64": str(2**62 + 9999)}
    default = studio.client.get(rows_url(studio, table), headers=studio.headers).json()
    assert len(default["rows"]) == 100


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=501", "limit=abc", "limit=", "cursor=bogus", "limit=5&limit=6", "page=2", "cursor=x:1"],
)
def test_rows_route_rejects_invalid_queries(studio, query):
    table = outputs(studio)["values"]
    url = f"/api/analysis/{studio.id}/outputs/{table['id']}/rows?{query}"
    response = studio.client.get(url, headers=studio.headers)
    assert response.status_code == 400 and response.json()["error"]


def test_rows_sort_and_filter_use_a_derived_index(studio):
    table = outputs(studio)["values"]
    cache = studio.service.store.root / "analysis-cache"
    response = studio.client.get(rows_url(studio, table, sort="value:desc", limit=5), headers=studio.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["indexed"] is True and body["row_count"] == 10000
    # Descending: NaN, +inf, then the largest finite values.
    assert [row["$record"]["value"] for row in body["rows"][:3]] == [
        {"$float64": "nan"},
        {"$float64": "inf"},
        9999 / 2,
    ]
    assert cache.is_dir() and any(cache.rglob("*.sqlite"))
    following = studio.client.get(
        rows_url(studio, table, sort="value:desc", limit=5, cursor=body["next_cursor"]), headers=studio.headers
    ).json()
    assert following["rows"][0]["$record"]["value"] == 9996 / 2
    big = json.dumps({"$int64": str(2**62 + 7)})
    filtered = studio.client.get(rows_url(studio, table, filter=f"big:ge:{big},index:lt:9"), headers=studio.headers)
    assert [row["$record"]["index"] for row in filtered.json()["rows"]] == [7, 8]
    assert filtered.json()["row_count"] == 2
    empty = studio.client.get(rows_url(studio, table, sort="", filter=""), headers=studio.headers).json()
    assert empty["indexed"] is False
    for query in ({"sort": "missing:asc"}, {"filter": "index:eq:"}, {"sort": "value:up"}):
        bad = studio.client.get(rows_url(studio, table, **query), headers=studio.headers)
        assert bad.status_code == 400 and bad.json()["error"]
    unsorted_cursor = empty["next_cursor"]
    mixed = studio.client.get(rows_url(studio, table, sort="value:asc", cursor=unsorted_cursor), headers=studio.headers)
    assert mixed.status_code == 400


@pytest.mark.parametrize("failure", ["timeout", "cancelled"])
def test_rows_index_timeouts_ask_for_a_retry(studio, monkeypatch, failure):
    from simulatte.analysis.table_query import TableQueryCancelled
    from simulatte.studio import analysis_http

    def slow(*args, **kwargs):
        raise TimeoutError("index build timed out") if failure == "timeout" else TableQueryCancelled("cancelled")

    monkeypatch.setattr(analysis_http, "query_table", slow)
    table = outputs(studio)["values"]
    response = studio.client.get(rows_url(studio, table, sort="index:asc"), headers=studio.headers)
    assert response.status_code == 503 and "retry" in response.json()["error"]


def test_a_disconnected_client_cancels_its_index_build(studio, monkeypatch):
    import time
    from starlette.requests import Request
    from simulatte.analysis import table_query

    original, observed = table_query._run_child, []

    def blocked(config, directory, *, timeout, cancel):
        deadline = time.monotonic() + 10
        while not cancel():
            assert time.monotonic() < deadline, "the disconnect never reached the build"
            time.sleep(0.01)
        observed.append(True)
        return original(config, directory, timeout=timeout, cancel=cancel)

    async def disconnected(self):
        return True

    monkeypatch.setattr(table_query, "_run_child", blocked)
    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    table = outputs(studio)["values"]
    cache = studio.service.store.root / "analysis-cache"
    before = sorted(path for path in cache.rglob("*") if path.is_file())
    response = studio.client.get(rows_url(studio, table, sort="big:desc", filter="index:lt:77"), headers=studio.headers)
    assert observed == [True]
    assert response.status_code == 503 and "cancelled" in response.json()["error"]
    assert sorted(path for path in cache.rglob("*") if path.is_file()) == before and table_query._BUILDS == {}


def test_artifact_targets_stay_inside_the_execution_tree(tmp_path):
    from simulatte.studio import analysis_http

    assert analysis_http._target(tmp_path, "png/image.png") == tmp_path / "png/image.png"
    for relative in ("../x", "/etc/passwd", "png\\image.png", "png//image.png", "./png"):
        with pytest.raises(analysis_http.Refused):
            analysis_http._target(tmp_path, relative)


def test_rows_route_refuses_non_tables_and_unknown_outputs(studio):
    number = outputs(studio)["total"]
    base = f"/api/analysis/{studio.id}/outputs"
    assert studio.client.get(f"{base}/{number['id']}/rows", headers=studio.headers).status_code == 404
    assert studio.client.get(f"{base}/{'0' * 64}/rows", headers=studio.headers).status_code == 404
    assert studio.client.get(f"{base}/short/rows", headers=studio.headers).status_code == 404
    assert studio.client.get(f"{base}/{number['id']}/other", headers=studio.headers).status_code == 404
    assert studio.client.get(f"{base}/{number['id']}/csv?x=1", headers=studio.headers).status_code == 404
    missing = "00000000-0000-4000-8000-000000000000"
    assert (
        studio.client.get(f"/api/analysis/{missing}/outputs/{'a' * 64}/rows", headers=studio.headers).status_code == 404
    )
    assert studio.client.get("/api/analysis/not-a-uuid/outputs/x/rows", headers=studio.headers).status_code == 404
    assert studio.client.get(f"/api/analysis/{studio.id}", headers=studio.headers).status_code == 404
    assert studio.client.get(f"{base}/{number['id']}/rows").status_code == 403
    foreign = {**studio.headers, "Origin": "https://example.com"}
    assert studio.client.get(f"{base}/{number['id']}/rows", headers=foreign).status_code == 403


def test_csv_route_streams_the_whole_table_as_attachment(studio):
    table = outputs(studio)["values"]
    response = studio.client.get(f"/api/analysis/{studio.id}/outputs/{table['id']}/csv", headers=studio.headers)
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="values.csv"'
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"
    root = studio.service.store.root / "analysis-results" / studio.id / "values/table"
    expected = io.StringIO()
    DatasetReader(root, DatasetManifest.from_dict(json.loads((root / "manifest.json").read_text()))).to_csv(expected)
    assert response.text == expected.getvalue()
    assert response.text.count("\n") == 10001


def test_csv_stream_stops_and_reports_failures(studio, monkeypatch):
    from simulatte.studio import analysis_http

    class Reader:
        def to_csv(self, destination):
            destination.write("a" * (analysis_http.CSV_CHUNK + 1))
            destination.write("tail")

    assert "".join(analysis_http.csv_chunks(Reader())) == "a" * (analysis_http.CSV_CHUNK + 1) + "tail"

    class Broken:
        def to_csv(self, destination):
            destination.write("head\n")
            raise ValueError("Dataset chunk integrity failure")

    stream = analysis_http.csv_chunks(Broken())
    with pytest.raises(ValueError, match="integrity"):
        list(stream)

    class Endless:
        def to_csv(self, destination):
            while True:
                destination.write("x" * analysis_http.CSV_CHUNK)

    stream = analysis_http.csv_chunks(Endless())
    assert next(stream)
    time.sleep(0.5)  # the writer fills the bounded queue and waits
    stream.close()

    class Exact:
        def to_csv(self, destination):
            destination.write("b" * analysis_http.CSV_CHUNK)

    assert list(analysis_http.csv_chunks(Exact())) == ["b" * analysis_http.CSV_CHUNK]


def test_tables_must_match_their_listed_artifacts(studio, monkeypatch):
    from simulatte.studio import analysis_http

    table = outputs(studio)["values"]
    url = rows_url(studio, table)
    original = AnalysisStore.output

    def unlisted(self, identity):
        manifest = codec.from_json(codec.to_json(original(self, identity)))
        for output in manifest["outputs"]:
            output["artifacts"] = [a for a in output["artifacts"] if "chunk-00000001" not in a["path"]]
        return manifest

    monkeypatch.setattr(AnalysisStore, "output", unlisted)
    response = studio.client.get(url, headers=studio.headers)
    assert response.status_code == 404 and "not a listed artifact" in response.json()["error"]
    monkeypatch.setattr(AnalysisStore, "output", original)
    monkeypatch.setattr(analysis_http, "MAX_TABLE_MANIFEST", 1)
    response = studio.client.get(url, headers=studio.headers)
    assert response.status_code == 404 and "exceeds 4 MiB" in response.json()["error"]


def test_store_check_and_rename_refuse_unknown_identities(studio):
    analysis = AnalysisStore(studio.service.store)
    with pytest.raises(KeyError):
        analysis.check("missing", definition_id="x", parameters={}, generation_id="y")
    with pytest.raises(KeyError):
        analysis.rename("missing", "Name")
    with pytest.raises(ValueError, match="200 characters"):
        analysis.rename(studio.id, "x" * 201)


def test_removed_registrations_fall_back_to_their_identity(studio, monkeypatch):
    monkeypatch.setattr(studio.service, "workspace", None)
    reply = call(studio.server, "analysis.get", execution_id=studio.id)
    assert reply["definition"]["name"] == "e64ca951-1b69-47a0-b1c5-7cfb91d90755"
    assert "registration_key" not in reply["definition"]
    assert reply["execution"]["name"] == "Weekly report"
    items = call(studio.server, "analysis.list", limit=100)["items"]
    assert all("registration_key" not in item for item in items)
    assert {item["name"] for item in items} >= {"Weekly report", "e64ca951-1b69-47a0-b1c5-7cfb91d90755"}


@pytest.mark.parametrize(
    ("name", "path", "media"),
    [
        ("png", "png/image.png", "image/png"),
        ("svg", "svg/image.svg", "image/svg+xml"),
        ("figure", "figure/figure.json", "application/json"),
        ("total", "total/number.json", "application/json"),
        ("values", "values/table/chunk-00000000.msgpack", "application/octet-stream"),
    ],
)
def test_artifact_route_isolates_saved_files(studio, name, path, media):
    descriptor = next(a for a in outputs(studio)[name]["artifacts"] if a["path"] == path)
    url = f"/api/analysis/{studio.id}/artifacts/{path}"
    response = studio.client.get(url, headers=studio.headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == media
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert response.headers["cache-control"] == "no-store"
    assert len(response.content) == descriptor["length"]
    partial = studio.client.get(url, headers={**studio.headers, "Range": "bytes=0-3"})
    assert partial.status_code == 206 and partial.content == response.content[:4]
    assert partial.headers["content-range"] == f"bytes 0-3/{descriptor['length']}"
    head = studio.client.head(url, headers=studio.headers)
    assert head.status_code == 200 and head.content == b""


@pytest.mark.parametrize(
    "path",
    [
        "manifest.json",
        "png/../manifest.json",
        "png/%2e%2e/manifest.json",
        "/etc/passwd",
        "png/missing.png",
        "",
        "png\\image.png",
    ],
)
def test_artifact_route_refuses_unlisted_paths(studio, path):
    response = studio.client.get(f"/api/analysis/{studio.id}/artifacts/{path}", headers=studio.headers)
    assert response.status_code == 404


def test_artifact_route_refuses_queries_ranges_and_symlinks(studio):
    url = f"/api/analysis/{studio.id}/artifacts/png/image.png"
    assert studio.client.get(url + "?download=1", headers=studio.headers).status_code == 404
    assert studio.client.get(url).status_code == 403
    assert studio.client.get(url, headers={**studio.headers, "Range": "bytes=999-"}).status_code == 416
    rerun = call(studio.server, "analysis.rerun", execution_id=studio.id)
    identity = rerun["execution"]["id"]
    assert wait_state(studio.server, identity, TERMINAL) == "succeeded"
    target = studio.service.store.root / "analysis-results" / identity / "png/image.png"
    secret = target.parent / "secret.png"
    target.rename(secret)
    target.symlink_to(secret)
    response = studio.client.get(f"/api/analysis/{identity}/artifacts/png/image.png", headers=studio.headers)
    assert response.status_code == 404
    target.unlink()
    target.write_bytes(b"tampered")
    response = studio.client.get(f"/api/analysis/{identity}/artifacts/png/image.png", headers=studio.headers)
    assert response.status_code == 404
    manifest = studio.service.store.root / "analysis-results" / identity / "values/table/manifest.json"
    manifest.write_text(manifest.read_text() + " ")
    table = outputs(studio, identity)["values"]
    rows = studio.client.get(f"/api/analysis/{identity}/outputs/{table['id']}/rows", headers=studio.headers)
    assert rows.status_code == 404


def test_frame_asset_gets_its_own_policy_and_app_csp_only_gains_frame_src(studio):
    from simulatte.studio import auth

    frame = studio.client.get("/assets/plotly-frame.html")
    assert frame.status_code == 200
    assert frame.headers["content-security-policy"] == (
        "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; "
        "form-action 'none'; frame-ancestors 'self'"
    )
    app = studio.client.get("/")
    assert app.headers["content-security-policy"] == auth.CSP
    assert "frame-src 'self'" in auth.CSP
    assert studio.client.get("/assets/missing/plotly-frame.html").status_code == 404
    assert auth.artifact_headers("image/png")["Content-Security-Policy"] == "sandbox; default-src 'none'"


def test_static_assets_accept_a_null_origin_but_commands_and_state_do_not(studio):
    null = {"Origin": "null"}
    frame = studio.client.get("/assets/plotly-frame.html", headers=null)
    assert frame.status_code == 200
    assert studio.client.head("/assets/plotly-frame.html", headers=null).status_code == 200
    assert studio.client.get("/", headers=null).status_code == 200
    # Nothing else: not the state, not mutations, not the bootstrap page, not a second origin value.
    assert studio.client.get("/api/state", headers={**studio.headers, **null}).status_code == 403
    assert studio.client.put("/api/layout", headers={**studio.headers, **null}).status_code == 403
    assert studio.client.get("/bootstrap?token=x", headers=null).status_code == 403
    assert (
        studio.client.get("/assets/plotly-frame.html", headers=[("Origin", "null"), ("Origin", "null")]).status_code
        == 403
    )
    assert studio.client.get("/assets/plotly-frame.html", headers={"Origin": "https://example.com"}).status_code == 403


def test_history_get_caps_at_200(studio):
    assert call(studio.server, "history.get", limit=200)["ok"]
    refused = call(studio.server, "history.get", limit=201)
    assert not refused["ok"] and "between 1 and 200" in refused["error"]["message"]


def test_unsupported_analysis_command_is_refused(studio):
    from simulatte.studio.analysis_commands import AnalysisCommands

    with pytest.raises(ValueError, match="Unsupported"):
        AnalysisCommands(studio.service)("analysis.unknown", {})


def test_studio_request_path_imports_no_analysis_code():
    script = (
        "import sys\n"
        "import simulatte.studio.project_commands, simulatte.studio.analysis_commands, simulatte.studio.analysis_http\n"
        "forbidden = ('simulatte.analysis.child', 'simulatte.analysis.adapters', 'simulatte.analysis.builtins',"
        " 'simulatte.analysis.command', 'matplotlib', 'plotly', 'seaborn')\n"
        "print([name for name in forbidden if name in sys.modules])\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
    sources = Path(__file__).parents[2] / "src/simulatte/studio"
    for path in sources.glob("*.py"):
        text = path.read_text()
        for loader in ("spec_from_file_location", "exec_module", "runpy", "import_module("):
            assert loader not in text, (path, loader)
        assert "simulatte.analysis.child" not in text and "simulatte.analysis.context" not in text, path


def test_read_only_service_lists_and_reads_but_never_mutates(tmp_path):
    from simulatte.studio.project_commands import ProjectServer

    root = tmp_path / "project"
    run, attempt = write_project(root)
    (root / "report.py").write_text(
        "from simulatte.analysis import analysis\nfrom simulatte.analysis.outputs import Number\n"
        "from simulatte.model import ParameterSchema\n"
        "@analysis(parameters=ParameterSchema(()))\n"
        "def analyze(context, parameters):\n    context.emit('total', Number(2**60))\n"
    )
    from simulatte.analysis.command import prepare_analysis

    with RunStore.open(root / ".simulatte") as store:
        environments = EnvironmentPool(root, store)
        definition, generation = prepare_analysis(store, "report", environments=environments)
        analysis = AnalysisStore(store)
        item = analysis.admit(
            analysis.preview(Selection(run_ids=(attempt,))).id,
            definition_id=definition,
            parameters={},
            generation_id=generation,
        )
        analysis.annotate(item.id, "saved")
        with AnalysisRunner(store, environments=environments) as runner:
            runner.submit(item.id)
            assert runner.wait(item.id, timeout=60).state == "succeeded"
    before = (root / ".simulatte/index.sqlite").read_bytes()
    service = ProjectService.open(StudioTarget(root, None), read_only=True)
    server = ProjectServer(service, 8123, static_dir(tmp_path / "static"))
    listed = call(server, "analysis.list")
    assert [i["id"] for i in listed["items"]] == [item.id] and listed["items"][0]["annotation"] == "saved"
    assert listed["items"][0]["name"] == "Report" and listed["items"][0]["registration_key"] == "report"
    reply = call(server, "analysis.get", execution_id=item.id)
    total = codec.from_json(reply["output"])["outputs"][0]
    for kind, body in (
        ("analysis.annotate", {"execution_id": item.id, "text": "x"}),
        ("analysis.rerun", {"execution_id": item.id}),
        ("analysis.cancel", {"execution_id": item.id}),
        ("analysis.refresh", {"execution_id": item.id}),
    ):
        refused = call(server, kind, **body)
        assert not refused["ok"] and "read-only" in refused["error"]["message"]
    assert server.analysis.changes() == [] and not server.analysis.watching()
    with TestClient(server.app, base_url=server.origin) as client:
        client.get(server.link())
        headers = {"X-Simulatte-Session": server.secret}
        number = client.get(f"/api/analysis/{item.id}/artifacts/total/number.json", headers=headers)
        assert number.status_code == 200 and codec.loads(number.content)["value"] == 2**60
        assert number.headers["content-security-policy"] == "sandbox; default-src 'none'"
        assert total["kind"] == "number"
    assert (root / ".simulatte/index.sqlite").read_bytes() == before


def test_read_only_tables_page_without_indexes_and_legacy_stores_are_empty(tmp_path, monkeypatch):
    from simulatte.studio.analysis_commands import AnalysisCommands
    from simulatte.studio.project_commands import ProjectServer

    root = tmp_path / "project"
    run, attempt = write_project(root)
    with RunStore.open(root / ".simulatte") as store:
        environments = EnvironmentPool(root, store)
        from simulatte.analysis.command import prepare_analysis

        definition, generation = prepare_analysis(store, "report", environments=environments)
        analysis = AnalysisStore(store)
        item = analysis.admit(
            analysis.preview(Selection(run_ids=(attempt,))).id,
            definition_id=definition,
            parameters={},
            generation_id=generation,
        )
        with AnalysisRunner(store, environments=environments) as runner:
            runner.submit(item.id)
            assert runner.wait(item.id, timeout=60).state == "succeeded"
    cache = root / ".simulatte/analysis-cache"
    cached_before = {p.relative_to(cache): p.read_bytes() for p in cache.rglob("*") if p.is_file()}
    service = ProjectService.open(StudioTarget(root, None), read_only=True)
    server = ProjectServer(service, 8123, static_dir(tmp_path / "static"))
    table = next(
        o
        for o in codec.from_json(call(server, "analysis.get", execution_id=item.id)["output"])["outputs"]
        if o["name"] == "values"
    )
    with TestClient(server.app, base_url=server.origin) as client:
        client.get(server.link())
        headers = {"X-Simulatte-Session": server.secret}
        url = f"/api/analysis/{item.id}/outputs/{table['id']}/rows"
        assert len(client.get(url + "?limit=7", headers=headers).json()["rows"]) == 7
        sorted_rows = client.get(url + "?sort=value:asc", headers=headers)
        assert sorted_rows.status_code == 409 and "writable" in sorted_rows.json()["error"]
        assert {p.relative_to(cache): p.read_bytes() for p in cache.rglob("*") if p.is_file()} == cached_before
        monkeypatch.setattr(service.reader, "version", 3)
        assert AnalysisCommands(service)("analysis.list", {}) == {"ok": True, "items": [], "next_cursor": None}
        with pytest.raises(KeyError):
            AnalysisCommands(service)("analysis.get", {"execution_id": item.id})
        legacy = client.get(f"/api/analysis/{item.id}/artifacts/total/number.json", headers=headers)
        assert legacy.status_code == 404 and "unavailable" in legacy.json()["error"]
    monkeypatch.setattr(service, "reader", None)
    with pytest.raises(ValueError, match="unavailable"):
        AnalysisCommands(service)("analysis.list", {})


def test_analysis_get_reports_the_inputs_observation_windows(tmp_path, monkeypatch):
    import hashlib

    from simulatte.analysis.datasets import DatasetWriter
    from simulatte.analysis.definition import AnalysisDefinition
    from simulatte.analysis.types import DatasetSchema, Field
    from simulatte.execution.store import _blob
    from simulatte.model import ParameterSchema
    from tests.core.test_experiment_results import build

    with RunStore.open(tmp_path / ".simulatte") as store:
        store.create_revision({})
        analysis = AnalysisStore(store)
        definition = analysis.put_definition(
            AnalysisDefinition("w", "report.py:run", "a" * 64, ParameterSchema(()), ())
        )
        with store._transaction() as db:
            db.execute("INSERT INTO environment_generations VALUES (?,?,?)", ("env", _blob({}), "selected"))
        run, _ = build(store, {(0, i): "pending" for i in range(4)})
        attempts = [publish(store, run, 0, i, i) for i in range(3)]
        # Two replications carry a measured dataset with its window; the third has scalars only.
        for attempt, start in zip(attempts[:2], (5.0, 5.0), strict=True):
            root = store.root / "results" / attempt / "artifacts"
            writer = DatasetWriter(root / "datasets" / "jobs", DatasetSchema("jobs", (Field("lateness", "float64"),)))
            writer.window = {"start": start, "end": 10.0, "limitations": []}
            writer.append({"lateness": 1.5})
            writer.finish()
            with store._transaction() as db:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        raw = path.read_bytes()
                        relative = path.relative_to(root).as_posix()
                        db.execute(
                            "INSERT INTO artifacts VALUES (?,?,?,?)",
                            (attempt, relative, len(raw), hashlib.sha256(raw).hexdigest()),
                        )
        preview = analysis.preview(Selection(experiment_run_ids=(run,)))
        execution = analysis.admit(preview.id, definition_id=definition, parameters={}, generation_id="env")
        original_loads = codec.loads

        def small_loads(raw):
            decoded = original_loads(raw)
            assert not isinstance(decoded, dict) or "replications" not in decoded, "whole snapshot decoded"
            return decoded

        monkeypatch.setattr(codec, "loads", small_loads)
        from simulatte.analysis import datasets

        monkeypatch.setattr(datasets, "Chunk", lambda **kw: pytest.fail("unrelated chunk materialized"))
        windows = analysis.get(execution.id)["snapshot"]["windows"]
        # The scalar-only replication reports the stopping policy's warm-up and horizon; the pending one nothing.
        assert windows == {
            "items": [
                {"dataset": None, "start": 0.0, "end": 10.0, "replications": 3},
                {"dataset": "jobs", "start": 5.0, "end": 10.0, "replications": 2},
            ],
            "total": 2,
        }
