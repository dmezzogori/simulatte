"""Setup and recovery are usable without importing or running project models."""

import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest
from starlette.testclient import TestClient

from simulatte.projects import StudioTarget, install_starter, starter_files, load_workspace
from simulatte.execution.store import RunStore, _blob, _read
from simulatte.projects.environments import EnvironmentPool


def api():
    assert importlib.util.find_spec("simulatte.projects.service"), "project service missing"
    return importlib.import_module("simulatte.projects.service")


def test_open_and_setup_are_explicit_and_never_run(tmp_path):
    service = api().ProjectService.open(StudioTarget(tmp_path, None))
    try:
        assert service.state()["phase"] == "setup"
        assert service.coordinator is None
        preview = service.configure({"starter": "production"})
        assert not (tmp_path / "pyproject.toml").exists()
        assert "models/production.py" in preview["files"]
        service.configure({"apply": preview["preview_id"]})
        assert load_workspace(tmp_path).models[0].name == "Production"
        assert service.coordinator is None
        assert service.state()["history"] == []
        assert service.store._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    finally:
        service.close()
    with RunStore.open(tmp_path / ".simulatte"):
        pass


def test_recovery_viewer_never_imports_project(tmp_path):
    m = api()
    install_starter(tmp_path, starter_files("production"))
    (tmp_path / "models/production.py").write_text('raise AssertionError("must never import")')
    shutil.copytree(Path(__file__).parents[1] / "fixtures/stores/project-v2", tmp_path / ".simulatte")
    with RunStore.open(tmp_path / ".simulatte") as store:
        pool = EnvironmentPool(tmp_path, store)
        generation = pool.use_existing(Path(sys.executable))
        evidence = _read(
            store._db.execute("SELECT evidence FROM environment_generations WHERE id=?", (generation.id,)).fetchone()[0]
        )
        evidence["spec"]["interpreter"] = str(tmp_path / "missing-python")
        with store._transaction() as db:
            db.execute(
                "UPDATE environment_generations SET evidence=?,state='selected' WHERE id=?",
                (_blob(evidence), generation.id),
            )
    before = (tmp_path / ".simulatte/index.sqlite").read_bytes()
    service = m.ProjectService.open(StudioTarget(tmp_path, None))
    try:
        state = service.state()
        assert state["read_only"] and state["phase"] == "recovery"
        assert state["history"] and service.coordinator is None
        item = state["history"][0]
        assert service.reader.result(item["id"]).path.joinpath("run.simtrace").is_file()
        assert service.reader.artifacts(item["id"])
        with pytest.raises(ValueError, match="read-only"):
            service.configure({"starter": "logistics"})
    finally:
        service.close()
    assert (tmp_path / ".simulatte/index.sqlite").read_bytes() == before


def test_unknown_store_and_locked_writer_are_actionable(tmp_path):
    m = api()
    with RunStore.open(tmp_path / ".simulatte"):
        service = m.ProjectService.open(StudioTarget(tmp_path, None))
        try:
            assert service.state()["read_only"]
            assert "coordinator" in service.state()["error"]
        finally:
            service.close()
    with sqlite3.connect(tmp_path / ".simulatte/index.sqlite") as db:
        db.execute("PRAGMA user_version=6")
    before = (tmp_path / ".simulatte/index.sqlite").read_bytes()
    service = m.ProjectService.open(StudioTarget(tmp_path, None))
    try:
        assert "schema 6" in service.state()["error"]
        assert service.coordinator is None
    finally:
        service.close()
    assert (tmp_path / ".simulatte/index.sqlite").read_bytes() == before


def test_setup_http_auth_origin_and_idempotent_commands(tmp_path):
    service = api().ProjectService.open(StudioTarget(tmp_path, None))
    from simulatte.studio.project_commands import ProjectServer

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<main>Studio</main>")
    (static / "manifest.json").write_text(json.dumps({"assets": {"index.html": "unused"}}))
    server = ProjectServer(service, 8123, static)
    with TestClient(server.app, base_url=server.origin) as client:
        assert client.get("/api/state").status_code == 403
        assert client.get(server.link()).status_code == 200
        headers = {"X-Simulatte-Session": server.secret}
        assert client.get("/api/state", headers=headers).json()["project"]["phase"] == "setup"
        assert client.get("/api/state", headers={**headers, "Origin": "https://example.com"}).status_code == 403
        response = client.get("/", headers=headers)
        assert "default-src 'none'" in response.headers["content-security-policy"]
        with client.websocket_connect(
            server.origin.replace("http:", "ws:") + "/ws", headers={"Origin": server.origin}
        ) as socket:
            socket.send_json({"v": 1, "type": "auth", "secret": server.secret})
            while socket.receive_json()["type"] != "state":
                pass
            message = {"v": 1, "type": "project.configure", "id": "preview", "body": {"starter": "production"}}
            socket.send_json(message)
            result = socket.receive_json()
            while result["type"] != "response":
                result = socket.receive_json()
            socket.send_json(message)
            repeated = socket.receive_json()
            while repeated["type"] != "response":
                repeated = socket.receive_json()
            assert result["body"]["preview_id"] == repeated["body"]["preview_id"]
            assert not (tmp_path / "pyproject.toml").exists()


def test_runtime_probe_rejects_missing_studio_and_bad_assets(tmp_path, monkeypatch):
    api()
    from simulatte.projects import bootstrap
    from types import SimpleNamespace

    for payload in (
        {"protocol": 999, "assets": True, "studio": True},
        {"protocol": 1, "assets": False, "studio": True},
        {"protocol": 1, "assets": True, "studio": False},
    ):
        monkeypatch.setattr(
            bootstrap.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        )
        with pytest.raises(ValueError, match="runtime|Studio|assets|protocol"):
            bootstrap.validate_runtime(Path(sys.executable))


def test_recovery_saved_run_and_artifact_ranges(tmp_path):
    from simulatte.studio.project_commands import ProjectServer

    shutil.copytree(Path(__file__).parents[1] / "fixtures/stores/project-v2", tmp_path / ".simulatte")
    service = api().ProjectService.open(StudioTarget(tmp_path, None), read_only=True)
    static = tmp_path / "static"
    static.mkdir()
    (static / "manifest.json").write_text('{"assets":{}}')
    server = ProjectServer(service, 8123, static)
    attempt = service.state()["history"][0]["id"]
    details = server._command({"type": "run.get", "body": {"attempt_id": attempt}}, None)
    assert details["ok"] and details["run"]["trace"]
    assert details["run"]["artifacts"][0]["path"] == "answer.txt"
    with TestClient(server.app, base_url=server.origin) as client:
        client.get(server.link())
        url = f"/api/artifacts/{attempt}/answer.txt"
        headers = {"X-Simulatte-Session": server.secret, "Range": "bytes=0-1"}
        result = client.get(url, headers=headers)
        assert result.status_code == 206 and result.content == b"8"
        assert result.headers["content-range"] == "bytes 0-0/1"
        assert client.head(url, headers=headers).content == b""
        assert client.get(url).status_code == 403


def test_launcher_handoff_failure_keeps_recovery_and_cli_flags(tmp_path, monkeypatch):
    from simulatte.projects import bootstrap
    from simulatte.studio import command

    target = StudioTarget(tmp_path, "plant.py:build")
    monkeypatch.setattr(bootstrap, "selected_interpreter", lambda target: tmp_path / "python")
    monkeypatch.setattr(bootstrap, "validate_runtime", lambda path: {})
    seen = []

    def failed(*args, **kwargs):
        seen.append(kwargs["flags"])
        raise ValueError("handoff failed")

    monkeypatch.setattr(bootstrap, "handoff", failed)

    def serve(service, **kwargs):
        assert service.read_only and "handoff failed" in service.error
        assert service.coordinator is None

    monkeypatch.setattr(command, "serve_project", serve)

    def initial(prepared):
        return {}

    initial.flags = {"seed": 55}
    bootstrap.launch_project(target, port=0, no_browser=True, configure=initial)
    assert seen == [{"seed": 55}]


def test_cancelled_setup_has_no_coordinator_or_attempt(tmp_path, monkeypatch):
    install_starter(tmp_path, starter_files("production"))
    service = api().ProjectService.open(StudioTarget(tmp_path, None))
    seen = []
    monkeypatch.setattr(service.environments, "cancel", lambda: seen.append(True))
    try:
        assert service.prepare_environment({"cancel": True})["ok"]
        assert seen == [True] and service.coordinator is None and not service.state()["history"]
    finally:
        service.close()


def test_registration_previews_explicit_membership_and_preserves_source(tmp_path):
    (tmp_path / "plant.py").write_text('raise AssertionError("no import")')
    service = api().ProjectService.open(StudioTarget(tmp_path, str(tmp_path / "plant.py") + ":alternate"))
    try:
        assert service.state()["legacy_reference"] == "plant.py:alternate"
        preview = service.configure(
            {"models": [{"key": "plant", "name": "Plant", "entrypoint": "plant.py:alternate"}], "include": ["plant.py"]}
        )
        assert not (tmp_path / "pyproject.toml").exists()
        assert 'include = ["plant.py"]' in preview["files"]["pyproject.toml"]
        service.configure({"apply": preview["preview_id"]})
        assert load_workspace(tmp_path).models[0].entrypoint == "plant.py:alternate"
        assert "raise AssertionError" in (tmp_path / "plant.py").read_text()
    finally:
        service.close()


def test_project_model_http_layout_and_saved_run_share_one_store(tmp_path):
    from dataclasses import asdict
    from simulatte.studio.project_commands import ProjectServer
    from simulatte.execution.schema import AttemptKey

    install_starter(tmp_path, starter_files("production"))
    service = api().ProjectService.open(StudioTarget(tmp_path, None))
    generation = service.environments.use_existing(Path(sys.executable))
    service.environments.activate(generation.id)
    static = tmp_path / "static"
    static.mkdir()
    (static / "manifest.json").write_text('{"assets":{}}')
    server = ProjectServer(service, 8123, static)
    model = service.workspace.models[0].id
    service.prepare_model(model)
    context = service.coordinator.contexts[model]
    assert not service.state()["history"]
    message = {"type": "run.start", "body": {"model_id": model}, "revision": asdict(context.revision)}
    started = server._command(message, None)
    service.coordinator.wait(AttemptKey(**started["key"]), timeout=10)
    assert len(service.state()["history"]) == 1
    assert server.snapshot()["project"]["models"][0]["view"] is not None
    with TestClient(server.app, base_url=server.origin) as client:
        client.get(server.link())
        headers = {
            "X-Simulatte-Session": server.secret,
            "Origin": server.origin,
            "X-Simulatte-Command": "layout-preview",
            "X-Simulatte-Model": model,
            "X-Simulatte-Revision": json.dumps(asdict(context.revision)),
        }
        layer = {"schema_version": "1.0", "placements": {"wc-0": {"kind": "server", "visual": {"label": "Reviewed"}}}}
        assert client.put("/api/layout/validate", json=layer).status_code == 403
        reply = client.put("/api/layout/validate", json=layer, headers=headers)
        assert reply.status_code == 200 and reply.json()["ok"]
        repeated = client.put("/api/layout/validate", json=layer, headers=headers)
        assert repeated.json() == reply.json()
        assert not (tmp_path / "layouts/production.json").exists()
        headers["X-Simulatte-Command"] = "layout-write"
        saved = client.put("/api/layout", json=layer, headers=headers)
        assert saved.json()["ok"], saved.json()
        assert (tmp_path / "layouts/production.json").exists()
        assert len(service.state()["history"]) == 1


def test_pump_announces_run_lifecycle_as_run_state(tmp_path):
    from types import SimpleNamespace
    from simulatte.studio.project_commands import ProjectServer

    key = {"request_id": "r1", "attempt_id": "a1", "generation": 1}
    events = [
        {"type": "result", "key": key, "body": {"attempt_id": "a1"}},
        {"type": "failed", "key": key, "body": {"message": "boom"}},
        {"type": "cancelled", "key": key, "body": {}},
        {"type": "progress", "key": key, "body": {"time": 1.0}},
    ]
    sent = []
    server = ProjectServer.__new__(ProjectServer)
    server.service = SimpleNamespace(
        coordinator=SimpleNamespace(contexts={}, events=lambda: events), restart_generation=1
    )
    server.hub = SimpleNamespace(notify=lambda kind, body, **fields: sent.append((kind, body, fields)), connections=())
    server.analysis = SimpleNamespace(watching=lambda: False)
    server.presentations = SimpleNamespace(changes=lambda: [])
    server._monitors = {}
    server._tasks = []
    server.on_restart = lambda: None
    asyncio.run(server._pump_project())
    assert [(kind, body["kind"], body["status"]) for kind, body, _ in sent[:3]] == [
        ("run_state", "result", "succeeded"),
        ("run_state", "failed", "failed"),
        ("run_state", "cancelled", "cancelled"),
    ]
    assert sent[1][1]["details"] == {"message": "boom"} and sent[1][2] == {"key": key}
    assert sent[3][0] == "progress"


def measured_server(tmp_path):
    """A two-model workspace whose model ``a`` declares the ``numbers`` measurement plan."""
    from simulatte.studio.project_commands import ProjectServer
    from tests.execution.test_driver import measured_model
    from tests.execution.test_project_execution import project

    workspace = project(tmp_path)
    (tmp_path / "a.py").write_text(measured_model())
    service = api().ProjectService.open(StudioTarget(tmp_path, None))
    generation = service.environments.use_existing(Path(sys.executable))
    service.environments.activate(generation.id)
    static = tmp_path / "static"
    static.mkdir()
    (static / "manifest.json").write_text('{"assets":{}}')
    model = workspace.models[0].id
    service.prepare_model(model)
    return service, ProjectServer(service, 8123, static), model


def test_run_start_selects_a_declared_measurement_plan(tmp_path):
    from dataclasses import asdict
    from simulatte.analysis import DatasetSchema, Field, MeasurementPlan
    from simulatte.analysis.measurements import select_measurements
    from simulatte.execution.schema import AttemptKey, _plain
    from tests.execution.test_project_execution import wait_until

    service, server, model = measured_server(tmp_path)
    try:
        context = service.coordinator.contexts[model]

        def run(**fields):
            # The previous attempt releases the interactive slot shortly after its result.
            wait_until(lambda: service.coordinator._owner() is None)
            message = {
                "type": "run.start",
                "body": {"model_id": model, **fields},
                "revision": asdict(context.revision),
            }
            started = server._command(message, None)
            assert started["ok"] and started["status"] == "started", started
            key = AttemptKey(**started["key"])
            return key, service.coordinator.wait(key, timeout=15)

        # A legacy body (no field) and an explicit null keep the unmeasured request identity.
        legacy, plain = run()
        assert run(measurements=None)[0].request_id == legacy.request_id
        assert not (plain.path / "artifacts/datasets").exists()
        details = server._command({"type": "run.get", "body": {"attempt_id": legacy.attempt_id}}, None)["run"]
        assert "measurements" not in details["request"] and "measurement_plan" not in details

        measured, result = run(measurements={"plan": "numbers"})
        assert measured.request_id != legacy.request_id
        request = service.store.request(measured.request_id)
        plan = MeasurementPlan("numbers", (DatasetSchema("numbers", (Field("x", "int64"),)),))
        # The same selection as the Python API's ``select_measurements(plan)``.
        assert _plain(request.measurements) == select_measurements(plan)
        assert (result.path / "artifacts/datasets/numbers/manifest.json").is_file()
        details = server._command({"type": "run.get", "body": {"attempt_id": measured.attempt_id}}, None)["run"]
        assert details["measurement_plan"] == "numbers"
        assert details["request"]["measurements"]["plan"] == "numbers"
        assert any(item["path"].startswith("datasets/numbers/") for item in details["artifacts"])

        for kind, extra in (("run.start", {}), ("run.stop_and_run", {"owner": asdict(measured)})):
            for value in ({"plan": "absent"}, {"plan": 3}, {"name": "numbers"}, "numbers"):
                refused = asyncio.run(
                    server.command(
                        {
                            "type": kind,
                            "body": {"model_id": model, "measurements": value, **extra},
                            "revision": asdict(context.revision),
                        },
                        None,
                    )
                )
                assert not refused["ok"] and "measurement" in refused["error"]["message"], (kind, value)
        unknown = asyncio.run(
            server.command(
                {
                    "type": "run.start",
                    "body": {"model_id": model, "measurements": {"plan": "absent"}},
                    "revision": asdict(context.revision),
                },
                None,
            )
        )
        assert "numbers" in unknown["error"]["message"]
        assert len(service.state()["history"]) == 3
    finally:
        service.close()


def test_project_experiment_list_names_the_selected_measurement_plan(tmp_path):
    from simulatte.analysis.codec import to_json
    from tests.execution.test_store_slots import DEFINITION, configurations, replications

    service, server, _ = measured_server(tmp_path)
    try:
        store = service.store
        selection = {"schema": 1, "plan": "numbers", "selection": to_json({}), "definition_hash": "d" * 64}
        for name, extra in (("plain", {}), ("measured", {"measurements": selection})):
            identity = store.put_experiment({**DEFINITION, "name": name, **extra})
            store.create_experiment_run(identity, {}, configurations(), replications())
        listed = {item["name"]: item for item in server.snapshot()["experiments"]}
        assert listed["measured"]["measurement_plan"] == "numbers"
        assert "measurement_plan" not in listed["plain"]
    finally:
        service.close()


def experiment_runs(store, count):
    """``count`` experiment runs; pairs share a creation time so the id tie-break is exercised."""
    from tests.execution.test_store_slots import DEFINITION, configurations, replications

    for number in range(count):
        identity = store.put_experiment({**DEFINITION, "name": f"sweep-{number}"})
        run = store.create_experiment_run(identity, {}, configurations(), replications())
        with store._transaction() as db:
            db.execute("UPDATE experiment_runs SET created=? WHERE id=?", (1000.0 + number // 2, run))


def test_experiment_list_pages_every_run_with_stable_cursors(tmp_path):
    from types import SimpleNamespace
    from simulatte.analysis.codec import to_json
    from simulatte.execution import scheduler
    from simulatte.studio.experiments import ExperimentCommands, with_measurement_plans
    from tests.execution.test_store_slots import DEFINITION, configurations, replications

    with RunStore.open(tmp_path / ".simulatte") as store:
        experiment_runs(store, 25)
        commands = ExperimentCommands(SimpleNamespace(store=store))
        expected = with_measurement_plans(store, scheduler.runs(store, limit=100))
        assert len(expected) == 25 and scheduler.runs(store) == expected[:20]
        first = commands("experiment.list", {"limit": 10})
        assert first["ok"] and first["items"] == expected[:10] and type(first["next_cursor"]) is str
        # A run created between pages lands before the first page and leaves the cursor stable.
        selection = {"schema": 1, "plan": "numbers", "selection": to_json({}), "definition_hash": "d" * 64}
        identity = store.put_experiment({**DEFINITION, "name": "newest", "measurements": selection})
        newest = store.create_experiment_run(identity, {}, configurations(), replications())
        second = commands("experiment.list", {"cursor": first["next_cursor"], "limit": 10})
        assert second["items"] == expected[10:20]
        assert commands("experiment.list", {"cursor": first["next_cursor"], "limit": 10}) == second
        third = commands("experiment.list", {"cursor": second["next_cursor"], "limit": 10})
        assert third["items"] == expected[20:] and third["next_cursor"] is None
        top = commands("experiment.list", {})
        assert len(top["items"]) == 20 and top["items"][0]["run_id"] == newest
        assert top["items"][0]["measurement_plan"] == "numbers"
        assert top["items"] == with_measurement_plans(store, scheduler.runs(store))
        # The 21st-newest run, beyond the snapshot's list, is reachable by id.
        older = expected[20]
        assert older["run_id"] not in {item["run_id"] for item in top["items"]}
        assert commands("experiment.get", {"run_id": older["run_id"]}) == {"ok": True, "run": older}
        for body in (
            {"limit": 0},
            {"limit": 101},
            {"limit": "10"},
            {"limit": True},
            {"cursor": "nonsense"},
            {"cursor": 3},
            {"extra": 1},
        ):
            with pytest.raises(ValueError):
                commands("experiment.list", body)
        assert len(commands("experiment.list", {"limit": 100})["items"]) == 26
        with pytest.raises(KeyError):
            commands("experiment.get", {"run_id": "0" * 64})
        with pytest.raises(ValueError):
            commands("experiment.get", {"run_id": "x"})


def test_experiment_list_and_get_on_a_read_only_project(tmp_path):
    from simulatte.execution import scheduler
    from simulatte.studio.project_commands import ProjectServer
    from simulatte.studio.websocket import EXPERIMENT_COMMANDS, decode_message

    with RunStore.open(tmp_path / ".simulatte") as store:
        experiment_runs(store, 23)
        expected = scheduler.runs(store, limit=100)
    service = api().ProjectService.open(StudioTarget(tmp_path, None), read_only=True)
    static = tmp_path / "static"
    static.mkdir()
    (static / "manifest.json").write_text('{"assets":{}}')
    server = ProjectServer(service, 8123, static)
    try:
        assert service.reader is not None and service.store is None
        assert {"experiment.list", "experiment.get"} <= EXPERIMENT_COMMANDS
        for kind in ("experiment.list", "experiment.get"):
            # Experiment commands need no revision in their envelope.
            decode_message(json.dumps({"v": 1, "type": kind, "id": "c1", "body": {}}))

        def command(kind, body):
            return asyncio.run(server.command({"type": kind, "body": body}, None))

        assert len(server.snapshot()["experiments"]) == 20
        page = command("experiment.list", {"limit": 20})
        assert page["ok"] and page["items"] == expected[:20]
        rest = command("experiment.list", {"cursor": page["next_cursor"]})
        assert rest == {"ok": True, "items": expected[20:], "next_cursor": None}
        assert command("experiment.get", {"run_id": expected[22]["run_id"]}) == {"ok": True, "run": expected[22]}
        missing = command("experiment.get", {"run_id": "0" * 64})
        assert missing["ok"] is False and missing["error"]["code"] == "failed"
        refused = command("experiment.list", {"limit": 101})
        assert refused["ok"] is False and "limit" in refused["error"]["message"]
    finally:
        service.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_original_attempt_beyond_history_and_experiment_pages(tmp_path, read_only):
    from contextlib import ExitStack, closing
    from types import SimpleNamespace

    from simulatte.execution.project_store import ProjectStore
    from simulatte.execution.store_reader import StoreReader
    from simulatte.studio.project_commands import ProjectServer
    from tests.core.test_experiment_results import build

    with RunStore.open(tmp_path / ".simulatte") as store:
        store.create_revision({})
        old, attempts = build(store, {(c, i): {} for c in range(2) for i in range(30)})
        attempt = attempts[0, 0]
        for i in range(21):
            build(store, {(0, i + 30): {}})
        assert attempt not in {r["id"] for r in ProjectStore(store).history().items}
        # Reuse associates the same attempt with another study; the first indexed match remains stable.
        build(store, {(0, 0): "pending"})
        with store._transaction() as db:
            assert "replication_attempt" in {r["name"] for r in db.execute("PRAGMA index_list(replications)")}
            assert [r["name"] for r in db.execute("PRAGMA index_info(replication_attempt)")] == ["attempt"]
        with ExitStack() as stack:
            selected = stack.enter_context(closing(StoreReader.open(store.root))) if read_only else store
            server = SimpleNamespace(
                service=SimpleNamespace(store=None if read_only else selected, reader=selected if read_only else None)
            )
            statements = []
            selected._db.set_trace_callback(statements.append)
            try:
                run = ProjectServer._command(server, {"type": "run.get", "body": {"attempt_id": attempt}}, None)["run"]
            finally:
                selected._db.set_trace_callback(None)
            assert int(run["request"]["seed"]) == 0
            assert run["replication"] == {"run_id": old, "name": "sweep", "configuration": "x=1/base", "index": 0}
            lookups = [s for s in statements if "FROM replications r" in s]
            assert len(lookups) == 1 and "LIMIT 1" in lookups[0]
            plan = list(selected._db.execute("EXPLAIN QUERY PLAN " + lookups[0]))
            assert any("SEARCH r USING INDEX replication_attempt" in row["detail"] for row in plan)
            assert not any("TEMP B-TREE" in row["detail"] for row in plan)
