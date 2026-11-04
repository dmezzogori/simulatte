"""Run outside Git with installed core[studio]/native and the supplied model."""

import hashlib
import json
from pathlib import Path
import sys
import time

from starlette.testclient import TestClient

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.snapshot import discover_project
from simulatte.studio.app import StudioServer
from simulatte.studio.server import assets


def main(source, output):
    output.mkdir(parents=True, exist_ok=False)
    project = output.parent / "studio-project"
    project.mkdir()
    (project / "plant.py").write_bytes(source.read_bytes())
    (project / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    coordinator = Coordinator(discover_project(project / "plant.py", project))
    static = assets()
    server = StudioServer(
        coordinator,
        8123,
        static,
        timeout=30,
        configure=lambda _: dict(level="kpi", parameters=dict(retention="bounded", kind="slow")),
    )
    with TestClient(server.app, base_url=server.origin) as client:
        assert client.get(server.link()).status_code == 200
        shell = client.get("/")
        assert shell.status_code == 200 and 'id="root"' in shell.text
        headers = {"X-Simulatte-Session": server.secret}
        assert client.get("/api/state").status_code == 403
        deadline = time.monotonic() + 30
        prefix = None
        while time.monotonic() < deadline:
            state = client.get("/api/state", headers=headers).json()
            if state["startup"]["phase"] == "error":
                raise AssertionError(state["startup"])
            active = state["active"]
            if active and active["prefix"]:
                response = client.get(active["prefix"]["url"], headers=headers)
                if response.status_code == 200:
                    path = output / "live-prefix.simtrace"
                    path.write_bytes(response.content)
                    trace = Trace.open(path)
                    if trace.kpi_series() and trace.cursor_range[-1][0] < 20:
                        prefix = active["prefix"]
                        break
            time.sleep(0.01)
        assert prefix is not None, "no real native observed prefix before completion"
        partial = (output / "live-prefix.simtrace").read_bytes()
        while time.monotonic() < deadline:
            state = client.get("/api/state", headers=headers).json()
            if state["results"]:
                break
            time.sleep(0.01)
        assert state["results"], state
        result = state["results"][0]
        response = client.get(result["trace"]["url"], headers=headers)
        assert response.status_code == 200 and response.content.startswith(partial)
        complete = output / "live-completed.simtrace"
        complete.write_bytes(response.content)
        trace = Trace.open(complete)
        fingerprint = trace.fingerprint
        assert fingerprint is not None
        assert trace.execution["initial"]["resolved"]["capabilities"]["execution_backend"]["experimental"] is True
        assert all(len(points) == 41 for points in trace.kpi_series().values())
        for name in json.loads((static / "manifest.json").read_text())["assets"]:
            assert client.get("/" + name).status_code == 200
    (project / "plant.py").unlink()
    with Coordinator.from_store(project, result["key"]["request_id"]) as repeated:
        reproduction = repeated.reproduce(result["key"]["request_id"], timeout=40)
        again = Trace.open(reproduction.path / "run.simtrace")
        assert again.fingerprint == fingerprint
        assert again.kpi_series() == trace.kpi_series() or all(
            len(a) == len(b)
            and all(x[0] == y[0] and (x[1] == y[1] or x[1] != x[1] and y[1] != y[1]) for x, y in zip(a, b))
            for a, b in zip(again.kpi_series().values(), trace.kpi_series().values())
        )
    (output / "studio.json").write_text(
        json.dumps(
            dict(
                passed=True,
                prefix=prefix,
                completed=result["trace"],
                fingerprint=dict(digest=fingerprint.digest, kpis=dict(fingerprint.kpis)),
                source_deleted_reproduction=reproduction.key.attempt_id,
                assets_sha256=hashlib.sha256((static / "manifest.json").read_bytes()).hexdigest(),
            ),
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(dict(passed=True, output=str(output))))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
