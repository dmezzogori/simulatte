"""Studio presentation and export commands: saved views, pinned exports and their notifications."""

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from simulatte.execution.store import RunStore
from simulatte.projects import StudioTarget
from simulatte.projects.environments import EnvironmentPool
from simulatte.projects.service import ProjectService
from simulatte.studio.presentation_commands import PRESENTATION_COMMANDS, READS, PresentationCommands
from tests.core.test_studio_analysis import static_dir
from tests.execution.test_presentation_store import OPTIONS, evidence, manifest, record, saved_output


def call(server, kind, **body):
    """Through the server's error mapping, like a websocket command."""
    return asyncio.run(server.command({"v": 1, "type": kind, "id": "t", "body": body}, None))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with RunStore.open(root / ".simulatte") as store:
        store.create_revision({})
        key = record(store, 1)
        generation = EnvironmentPool(root, store).use_existing(Path(sys.executable)).id
        execution, descriptor = saved_output(store, record(store, 2).attempt_id, generation=generation)
    return SimpleNamespace(root=root, key=key, execution=execution, descriptor=descriptor)


@pytest.fixture
def studio(project, tmp_path):
    from simulatte.studio.project_commands import ProjectServer

    service = ProjectService.open(StudioTarget(project.root, None))
    server = ProjectServer(service, 8125, static_dir(tmp_path / "static"))
    with TestClient(server.app, base_url=server.origin) as client:
        client.get(server.link())
        yield SimpleNamespace(**vars(project), service=service, server=server, client=client)


def test_commands_are_allowlisted_project_commands():
    from simulatte.studio.websocket import COMMANDS, PROJECT_COMMANDS

    assert PRESENTATION_COMMANDS <= PROJECT_COMMANDS <= COMMANDS
    assert READS == {"presentation.list", "presentation.get", "export.list", "export.get"}
    with pytest.raises(ValueError, match="Unsupported"):
        PresentationCommands(SimpleNamespace())("presentation.diff", {})


def test_save_revise_export_and_delete_through_studio(studio):
    server = studio.server
    item = evidence(studio.execution, studio.descriptor)
    created = call(server, "presentation.create", manifest=manifest(studio.key, [item]))
    assert created["ok"] and created["revision"] == 1
    identity = created["id"]
    listed = call(server, "presentation.list", limit=10)
    assert [entry["id"] for entry in listed["items"]] == [identity] and listed["next_cursor"] is None
    assert listed["items"][0]["recording"] == {"attempt_id": studio.key.attempt_id}
    revised = call(server, "presentation.revise", id=identity, expected_revision=1, manifest=manifest(studio.key))
    assert revised == {"ok": True, "revision": 2}
    stale = call(server, "presentation.revise", id=identity, expected_revision=1, manifest=manifest(studio.key))
    assert stale["error"] == {"code": "stale_revision", "message": "stale revision"}
    invalid = call(server, "presentation.create", manifest=manifest(studio.key, rate=0))
    assert invalid["error"]["code"] == "invalid_manifest" and invalid["error"]["path"] == "rate"
    missing = call(server, "presentation.create", manifest=manifest(studio.key, time={"start": 0, "end": 9}))
    assert missing["error"]["code"] == "failed" and "outside the recording" in missing["error"]["message"]

    admitted = call(server, "export.admit", presentation_id=identity, revision=1, target="video", options=OPTIONS)
    job = admitted["job"]
    assert (job["state"], job["revision"], job["target"], job["options"]) == ("waiting", 1, "video", OPTIONS)
    assert {"kind": "presentation-revision", "id": f"{identity}:1"} in job["references"]
    assert call(server, "export.start", job_id=job["id"])["job"]["state"] == "running"
    refused = call(server, "presentation.delete", id=identity)
    assert refused["error"]["code"] == "failed" and "active exports" in refused["error"]["message"]
    output = {"name": "view.mp4", "size": 10, "sha256": "9" * 64, "media_type": "video/mp4"}
    finished = call(server, "export.finish", job_id=job["id"], outcome={"state": "succeeded", "output": output})
    assert finished["job"]["state"] == "succeeded" and finished["job"]["references"] == []
    other = call(server, "export.admit", presentation_id=identity, revision=2, target="studio", options={}, statics=[])
    assert call(server, "export.cancel", job_id=other["job"]["id"])["job"]["state"] == "cancelled"
    assert call(server, "export.get", job_id=job["id"])["job"] == finished["job"]
    assert [j["id"] for j in call(server, "export.list", presentation_id=identity)["items"]] == [
        other["job"]["id"],
        job["id"],
    ]
    assert len(call(server, "export.list")["items"]) == 2

    got = call(server, "presentation.get", id=identity)
    assert got["manifest"]["revision"] == 2 and got["manifest"]["evidence"] == []
    assert [r["revision"] for r in got["revisions"]] == [1, 2]
    assert [j["id"] for j in got["exports"]] == [other["job"]["id"], job["id"]]
    assert call(server, "presentation.get", id=identity, revision=1)["manifest"]["evidence"] == [item]
    assert call(server, "presentation.delete", id=identity) == {"ok": True}
    assert call(server, "presentation.get", id=identity)["error"]["code"] == "failed"
    assert call(server, "presentation.list")["items"] == []

    assert server.presentations.watching()
    changes = server.presentations.changes()
    assert not server.presentations.watching() and server.presentations.changes() == []
    assert changes == [
        ("presentation_changed", {"id": identity, "revision": 1}),
        ("presentation_changed", {"id": identity, "revision": 2}),
        ("export_state", {"job_id": job["id"], "presentation_id": identity, "state": "waiting"}),
        ("export_state", {"job_id": job["id"], "presentation_id": identity, "state": "running"}),
        ("export_state", {"job_id": job["id"], "presentation_id": identity, "state": "succeeded"}),
        ("export_state", {"job_id": other["job"]["id"], "presentation_id": identity, "state": "waiting"}),
        ("export_state", {"job_id": other["job"]["id"], "presentation_id": identity, "state": "cancelled"}),
        ("presentation_changed", {"id": identity, "revision": None}),
    ]


def test_notifications_reach_connected_clients_through_the_pump(studio):
    origin = studio.server.origin
    with studio.client.websocket_connect(origin.replace("http:", "ws:") + "/ws", headers={"Origin": origin}) as socket:
        socket.send_json({"v": 1, "type": "auth", "secret": studio.server.secret})
        body = {"manifest": manifest(studio.key)}
        socket.send_json({"v": 1, "type": "presentation.create", "id": "c1", "body": body})
        identity, seen = None, None
        while seen is None:
            message = socket.receive_json()
            if message["type"] == "response" and message["id"] == "c1":
                assert message["body"]["ok"], message
                identity = message["body"]["id"]
            elif message["type"] == "presentation_changed":
                seen = message["body"]
        assert identity is not None and seen == {"id": identity, "revision": 1}
        socket.send_json(
            {
                "v": 1,
                "type": "export.admit",
                "id": "e1",
                "body": {"presentation_id": identity, "revision": 1, "target": "html", "options": {}, "statics": []},
            }
        )
        while True:
            message = socket.receive_json()
            if message["type"] == "export_state":
                assert message["body"]["presentation_id"] == identity and message["body"]["state"] == "waiting"
                break


def test_read_only_projects_list_and_get_but_refuse_changes(project, tmp_path):
    from simulatte.studio.project_commands import ProjectServer

    with RunStore.open(project.root / ".simulatte") as store:
        from simulatte.presentations.store import PresentationStore

        identity = PresentationStore(store).create(manifest(project.key))
        job = PresentationStore(store).admit_export(identity, 1, "video", OPTIONS, []).id
    tables = ("presentations", "presentation_revisions", "export_jobs", '"references"')

    def rows(store):
        return {table: [tuple(r) for r in store._db.execute(f"SELECT * FROM {table}")] for table in tables}

    with RunStore.open(project.root / ".simulatte") as writer:
        before = rows(writer)
        service = ProjectService.open(StudioTarget(project.root, None))
        try:
            assert service.read_only and service.reader is not None
            server = ProjectServer(service, 8126, static_dir(tmp_path / "static"))
            assert [item["id"] for item in call(server, "presentation.list")["items"]] == [identity]
            assert call(server, "presentation.get", id=identity)["manifest"]["id"] == identity
            # Reopening the writer recovered the admitted job: it is lost, never finished.
            assert call(server, "export.get", job_id=job)["job"]["state"] == "lost"
            assert [item["id"] for item in call(server, "export.list")["items"]] == [job]
            for kind, body in (
                ("presentation.create", {"manifest": manifest(project.key)}),
                ("presentation.revise", {"id": identity, "expected_revision": 1, "manifest": manifest(project.key)}),
                ("presentation.delete", {"id": identity}),
                ("export.admit", {"presentation_id": identity, "revision": 1, "target": "video", "options": {}}),
                ("export.start", {"job_id": job}),
                ("export.finish", {"job_id": job, "outcome": {"state": "failed"}}),
                ("export.cancel", {"job_id": job}),
            ):
                refused = call(server, kind, **body)
                assert refused["error"]["code"] == "failed" and "read-only" in refused["error"]["message"], kind
            assert not server.presentations.watching()
        finally:
            service.close()
        assert rows(writer) == before


def test_unavailable_store_is_reported(tmp_path):
    commands = PresentationCommands(SimpleNamespace(store=None, reader=None))
    for kind, body in (
        ("presentation.list", {}),
        ("presentation.get", {"id": str(uuid4())}),
        ("export.list", {}),
        ("export.get", {"job_id": str(uuid4())}),
    ):
        with pytest.raises(ValueError, match="unavailable"):
            commands(kind, body)
