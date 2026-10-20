"""Studio ``presentation.diff|record|recordings`` commands and the ``recording_state`` notification."""

import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

import pytest

from simulatte.execution.schema import AttemptKey, request_id
from simulatte.projects import StudioTarget
from simulatte.projects.service import ProjectService
from tests.execution.test_project_execution import project, wait_until


def serve(tmp_path, *, read_only=False):
    from simulatte.studio.project_commands import ProjectServer

    service = ProjectService.open(StudioTarget(tmp_path, None), read_only=read_only)
    if not read_only:
        generation = service.environments.use_existing(Path(sys.executable))
        service.environments.activate(generation.id)
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    (static / "manifest.json").write_text('{"assets":{}}')
    return service, ProjectServer(service, 8123, static)


def original_run(service, server, model):
    service.prepare_model(model)
    context = service.coordinator.contexts[model]
    started = server._command(
        {"type": "run.start", "body": {"model_id": model}, "revision": asdict(context.revision)}, None
    )
    key = AttemptKey(**started["key"])
    service.coordinator.wait(key, timeout=15)
    wait_until(lambda: service.coordinator._owner() is None)
    return key, service.store.request(key.request_id)


def command(server, kind, body):
    return asyncio.run(server.command({"type": kind, "body": body}, None))


def attempts(store):
    with store._transaction() as db:
        return db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]


def test_diff_before_record_then_completion_and_listing(tmp_path):
    workspace = project(tmp_path)
    service, server = serve(tmp_path)
    try:
        key, request = original_run(service, server, workspace.models[0].id)
        co, seed = service.coordinator, request.seed
        before = attempts(service.store)
        fresh = command(server, "presentation.diff", {"attempt_id": key.attempt_id, "seed": seed + 1})
        assert fresh == {
            "ok": True,
            "mode": "new_seed",
            "diff": [{"field": "seed", "original": str(seed), "target": str(seed + 1)}],
            "target_request_id": request_id(replace(request, seed=seed + 1)),
        }
        same = command(server, "presentation.diff", {"attempt_id": key.attempt_id, "seed": seed})
        assert (same["mode"], same["diff"], same["target_request_id"]) == ("replay", [], key.request_id)
        edited = command(
            server,
            "presentation.diff",
            {"attempt_id": key.attempt_id, "seed": seed, "overrides": {"parameters": {"n": 5}}},
        )
        assert edited["mode"] == "edited"
        assert edited["diff"] == [{"field": "parameters.n", "original": 2, "target": 5}]
        # Showing the differences runs nothing.
        assert attempts(service.store) == before
        for body, message in (
            ({"attempt_id": key.attempt_id}, "Expected only the fields"),
            ({"attempt_id": key.attempt_id, "seed": 1, "extra": 1}, "Expected only the fields"),
            ({"attempt_id": key.attempt_id, "seed": 1, "overrides": {"source_hash": "d" * 64}}, "new preparation"),
            ({"attempt_id": key.attempt_id, "seed": -1}, "seed"),
            ({"attempt_id": "nope", "seed": 1}, "attempt id"),
        ):
            refused = command(server, "presentation.diff", body)
            assert refused["ok"] is False and message in refused["error"]["message"], (body, refused)
        refused = command(server, "presentation.record", {"attempt_id": key.attempt_id, "seed": 1, "worker": 3})
        assert refused["ok"] is False and "worker" in refused["error"]["message"]
        assert attempts(service.store) == before

        recorded = command(server, "presentation.record", {"attempt_id": key.attempt_id, "seed": seed})
        assert recorded["ok"], recorded
        job = recorded["job"]
        assert (job["original_attempt"], job["mode"], job["diff"]) == (key.attempt_id, "replay", [])
        assert job["key"]["request_id"] == key.request_id
        states = []

        def finished():
            states.extend(event["body"] for event in co.events() if event["type"] == "recording_state")
            return any(state["status"] != "running" for state in states)

        wait_until(finished)
        assert states == [
            {
                "attempt_id": job["key"]["attempt_id"],
                "original_attempt": key.attempt_id,
                "mode": "replay",
                "status": status,
                "verified": verified,
            }
            for status, verified in (("running", None), ("succeeded", True))
        ]
        wait_until(lambda: not server.recordings._active)
        listed = command(server, "presentation.recordings", {})
        assert listed["ok"] is True
        assert [(item["attempt_id"], item["mode"], item["status"], item["verified"]) for item in listed["items"]] == [
            (job["key"]["attempt_id"], "replay", "succeeded", True)
        ]
        assert command(server, "presentation.recordings", {"original_attempt": key.attempt_id}) == listed
        assert command(server, "presentation.recordings", {"original_attempt": "f" * 32})["items"] == []
        assert command(server, "presentation.recordings", {"original_attempt": "f"})["ok"] is False
        # A repeated request while the same recording is active returns that recording.
        target = request_id(replace(request, seed=seed + 1))
        pending = {"key": job["key"], "original_attempt": key.attempt_id, "mode": "new_seed", "diff": []}
        server.recordings._active[(key.attempt_id, target)] = pending
        before = attempts(service.store)
        again = command(server, "presentation.record", {"attempt_id": key.attempt_id, "seed": seed + 1})
        assert again == {"ok": True, "job": pending} and attempts(service.store) == before
        # A recording that cannot start answers the command itself.
        server.recordings._active.clear()
        failed = command(server, "presentation.record", {"attempt_id": "0" * 32, "seed": 1})
        assert failed["ok"] is False and not server.recordings._active
    finally:
        service.close()


def test_read_only_project_lists_recordings_and_refuses_new_ones(tmp_path):
    from simulatte.presentations.recording import record_presentation

    workspace = project(tmp_path)
    service, server = serve(tmp_path)
    try:
        key, request = original_run(service, server, workspace.models[0].id)
        job = record_presentation(service.coordinator, key.attempt_id, seed=request.seed + 3, timeout=20)
    finally:
        service.close()
    service, server = serve(tmp_path, read_only=True)
    try:
        assert service.reader is not None and service.store is None
        listed = command(server, "presentation.recordings", {})
        assert [(item["attempt_id"], item["mode"], item["status"]) for item in listed["items"]] == [
            (job.key.attempt_id, "new_seed", "succeeded")
        ]
        diff = command(server, "presentation.diff", {"attempt_id": key.attempt_id, "seed": request.seed + 3})
        assert diff["ok"] and diff["target_request_id"] == job.key.request_id
        refused = command(server, "presentation.record", {"attempt_id": key.attempt_id, "seed": 4})
        assert refused["ok"] is False and "read-only" in refused["error"]["message"]
    finally:
        service.close()


def test_recording_commands_need_no_revision_and_their_state_reaches_clients(tmp_path):
    from types import SimpleNamespace
    from simulatte.studio.project_commands import ProjectServer
    from simulatte.studio.recording_commands import RECORDING_COMMANDS, RecordingCommands
    from simulatte.studio.websocket import PROJECT_COMMANDS, decode_message

    assert RECORDING_COMMANDS == {"presentation.diff", "presentation.record", "presentation.recordings"}
    assert RECORDING_COMMANDS <= PROJECT_COMMANDS
    for kind in sorted(RECORDING_COMMANDS):
        assert decode_message(json.dumps({"v": 1, "type": kind, "id": "c1", "body": {}}))["type"] == kind
    commands = RecordingCommands(SimpleNamespace(store=None, reader=None))
    for kind in ("presentation.recordings", "presentation.diff"):
        body = {} if kind == "presentation.recordings" else {"attempt_id": "0" * 32, "seed": 1}
        with pytest.raises(ValueError, match="unavailable"):
            commands(kind, body)
    with pytest.raises(ValueError, match="Unsupported"):
        commands("presentation.get", {})
    # The waiting thread swallows the failure ``finish_recording`` announced and forgets the recording.
    from simulatte.execution.coordinator import ExecutionFailure
    from simulatte.execution.schema import AttemptKey
    from simulatte.presentations.recording import RecordingJob

    def lost(key, *, timeout=None):
        raise ExecutionFailure(key, "lost", {"message": "worker lost"})

    announced = []
    job = RecordingJob(AttemptKey("a" * 64, "b" * 32, 1), "c" * 32, "new_seed", None)
    coordinator = SimpleNamespace(wait=lost, _emit=lambda kind, key, body: announced.append((kind, body["status"])))
    commands.service.timeout = None
    commands._active[("c" * 32, "a" * 64)] = {}
    commands._finish(coordinator, job, ("c" * 32, "a" * 64))
    assert announced == [("recording_state", "lost")] and commands._active == {}
    key = {"request_id": "r1", "attempt_id": "a1", "generation": 1}
    body = {"attempt_id": "a1", "original_attempt": "o1", "mode": "new_seed", "status": "succeeded", "verified": None}
    sent = []
    server = ProjectServer.__new__(ProjectServer)
    server.service = SimpleNamespace(
        coordinator=SimpleNamespace(
            contexts={}, events=lambda: [{"type": "recording_state", "key": key, "body": body}]
        ),
        restart_generation=1,
    )
    server.hub = SimpleNamespace(notify=lambda kind, body, **fields: sent.append((kind, body, fields)), connections=())
    server.analysis = SimpleNamespace(watching=lambda: False)
    server.presentations = SimpleNamespace(changes=lambda: [])
    server._monitors, server._tasks, server.on_restart = {}, [], lambda: None
    asyncio.run(server._pump_project())
    assert sent == [("recording_state", body, {"key": key})]
