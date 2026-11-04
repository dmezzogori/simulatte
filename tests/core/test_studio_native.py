"""Real native children feed Studio's authenticated committed trace transport."""

import json
import time

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.snapshot import discover_project
from simulatte.studio.app import StudioServer
from tests.core.test_studio_experiments import Socket
from tests.core.test_studio_live import auth, wait_state

MODEL = """from dataclasses import dataclass
import time
from simulatte import model
from simulatte.experimental.native import Job, NativeProduction, NativeObservation
@dataclass(frozen=True)
class Params:
    retention: str = "bounded"
    kind: str = "healthy"
@model(params=Params, horizon=20)
def build(env, layout, params):
    backend = NativeProduction(3,
        (Job(i // 3 * 0.5, (i % 3, (i + 1) % 3), (1, 0.5), i + 4) for i in range(36)),
        capacities=(2,1,3), retention=params.retention,
        observation=NativeObservation(interval=0.5, max_samples=41))
    env.attach_execution_backend(backend)
    if params.kind != "healthy":
        def pacing():
            for i in range(40):
                yield env.timeout(0.5)
                time.sleep(0.05)
                if params.kind == "failed" and i == 24:
                    raise ValueError("native Studio producer failure")
        env.process(pacing())
"""


def setup(tmp_path, retention, kind):
    pytest.importorskip("simulatte_native")
    project = tmp_path / "project"
    project.mkdir()
    (project / "plant.py").write_text(MODEL)
    (project / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    coordinator = Coordinator(discover_project(project / "plant.py", project))
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("Studio shell")
    (static / "manifest.json").write_text(json.dumps({"assets": {"index.html": "unused"}}))
    server = StudioServer(
        coordinator,
        8123,
        static,
        timeout=20,
        configure=lambda _: dict(level="kpi", parameters=dict(retention=retention, kind=kind)),
    )
    return coordinator, server


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_native_studio_final_series_metadata_and_replay_capability(tmp_path, retention):
    coordinator, server = setup(tmp_path, retention, "healthy")
    with TestClient(server.app, base_url=server.origin) as client:
        headers = auth(server, client)
        state = wait_state(client, headers, lambda value: bool(value["results"]))
        result = state["results"][0]
        data = client.get(result["trace"]["url"], headers=headers)
        assert data.status_code == 200
        path = tmp_path / "native.simtrace"
        path.write_bytes(data.content)
        trace = Trace.open(path)
        capabilities = trace.execution["initial"]["resolved"]["capabilities"]["execution_backend"]
        assert capabilities["experimental"] is True
        assert capabilities["observation"]["scheduled_samples"] == 41
        assert len(trace.kpi_series()) == 8 and all(len(rows) == 41 for rows in trace.kpi_series().values())
        assert trace.kpi_series()["shopfloor/utilization"][-1][1] == trace.kpis()["shopfloor/utilization"]
        socket = Socket(server, client)
        try:
            response = socket.send(
                "series.get", dict(attempts=[result["key"]["attempt_id"]], keys=["shopfloor/utilization"])
            )
            assert response["ok"] and response["series"][0]["series"]["shopfloor/utilization"]["truncated"] is False
            replay = socket.send("replay.start", dict(request_id=result["key"]["request_id"]))
            assert not replay["ok"] and "KPI observation only" in replay["error"]["message"]
        finally:
            socket.close()


@pytest.mark.parametrize("retention", ["full", "bounded"])
@pytest.mark.parametrize("terminal", ["cancelled", "failed"])
def test_native_studio_observed_prefix_revokes_without_publishing_a_bad_result(tmp_path, retention, terminal):
    coordinator, server = setup(tmp_path, retention, "slow" if terminal == "cancelled" else "failed")
    prefix = None
    with TestClient(server.app, base_url=server.origin) as client:
        headers = auth(server, client)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = client.get("/api/state", headers=headers).json()
            active = state["active"]
            if active and active["prefix"]:
                candidate = active["prefix"]
                response = client.get(candidate["url"], headers=headers)
                if response.status_code == 200:
                    path = tmp_path / "live.simtrace"
                    path.write_bytes(response.content)
                    trace = Trace.open(path)
                    if trace.kpi_series():
                        assert trace.fingerprint is None
                        prefix = candidate
                        break
            time.sleep(0.01)
        assert prefix is not None, "no observed committed native prefix before terminal state"
        if terminal == "cancelled":
            key = active["key"]
            socket = Socket(server, client)
            try:
                result = socket.send(
                    "run.cancel",
                    dict(request_id=key["request_id"]),
                    revision=state["revision"],
                    attempt_id=key["attempt_id"],
                    generation=key["generation"],
                )
                assert result["ok"]
            finally:
                socket.close()
        state = wait_state(
            client, headers, lambda value: value["latest_attempt"] and value["latest_attempt"]["status"] == terminal
        )
        assert not state["results"]
        assert client.get(prefix["url"], headers=headers).status_code == 410
