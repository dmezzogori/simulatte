"""One-use runtime handoff is authenticated before any writer is admitted."""

import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from simulatte.projects import bootstrap, StudioTarget
from simulatte.projects.environments import EnvironmentPool
from simulatte.execution.store import RunStore


CAPABILITY = {"protocol": 1, "studio": True, "assets": True}


@pytest.mark.parametrize("failure", [None, "token", "capability", "exit", "kill"])
def test_handoff_authenticates_child_and_cleans_failed_process(tmp_path, monkeypatch, failure):
    events = []
    threads = []

    class Child:
        def __init__(self, argv, *, env):
            payload = json.loads(argv[-1])
            assert payload["root"] == str(tmp_path)
            assert payload["flags"] == {"seed": 7}
            assert "SIMULATTE_HANDOFF_TOKEN" not in argv
            self.terminated = False
            self.killed = False

            def connect():
                with socket.create_connection(("127.0.0.1", payload["handshake_port"]), timeout=5) as connection:
                    with connection.makefile("rwb") as stream:
                        stream.write(
                            (
                                json.dumps(
                                    {
                                        "token": "wrong"
                                        if failure in ("token", "kill")
                                        else env["SIMULATTE_HANDOFF_TOKEN"],
                                        "capabilities": {} if failure == "capability" else CAPABILITY,
                                    }
                                )
                                + "\n"
                            ).encode()
                        )
                        stream.flush()
                        events.append(stream.readline())

            thread = threading.Thread(target=connect)
            threads.append(thread)
            thread.start()

        def wait(self, timeout=None):
            if failure == "kill" and self.terminated and not self.killed:
                raise subprocess.TimeoutExpired("test child", timeout)
            return 1 if failure == "exit" else 0

        def terminate(self):
            self.terminated = True
            events.append("terminate")

        def kill(self):
            self.killed = True
            events.append("kill")

    monkeypatch.setattr(bootstrap.subprocess, "Popen", Child)
    try:
        if failure is None:
            bootstrap.handoff(
                Path(sys.executable), StudioTarget(tmp_path, None), port=0, no_browser=True, flags={"seed": 7}
            )
        else:
            with pytest.raises(ValueError):
                bootstrap.handoff(
                    Path(sys.executable), StudioTarget(tmp_path, None), port=0, no_browser=True, flags={"seed": 7}
                )
    finally:
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
    if failure in (None, "exit"):
        assert b"start\n" in events
    else:
        assert b"start\n" not in events
    if failure:
        assert "terminate" in events
    if failure == "kill":
        assert "kill" in events


@pytest.mark.parametrize("mode", ["success", "denied", "wrong-interpreter"])
def test_child_validates_admission_and_generation_before_launch(tmp_path, monkeypatch, mode):
    with RunStore.open(tmp_path / ".simulatte") as store:
        pool = EnvironmentPool(tmp_path, store)
        generation = pool.use_existing(Path(sys.executable))
    payload = {
        "root": str(tmp_path),
        "legacy_reference": None,
        "port": 0,
        "no_browser": True,
        "generation": generation.id,
    }
    received = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        payload["handshake_port"] = listener.getsockname()[1]

        def admit():
            connection, _ = listener.accept()
            with connection, connection.makefile("rwb") as stream:
                received.append(json.loads(stream.readline()))
                stream.write(b"no\n" if mode == "denied" else b"start\n")
                stream.flush()

        thread = threading.Thread(target=admit)
        thread.start()
        monkeypatch.setenv("SIMULATTE_HANDOFF_TOKEN", "one-use-test-token")
        monkeypatch.setattr(sys, "argv", ["bootstrap", "--handoff", json.dumps(payload)])
        monkeypatch.setattr(bootstrap, "capabilities", lambda: CAPABILITY)
        launched = []
        monkeypatch.setattr(bootstrap, "launch_project", lambda *a, **kw: launched.append((a, kw)))
        if mode == "wrong-interpreter":
            monkeypatch.setattr(sys, "executable", str(tmp_path / "other-python"))
        try:
            if mode == "success":
                bootstrap.main()
            else:
                with pytest.raises(ValueError, match="admitted|interpreter"):
                    bootstrap.main()
        finally:
            thread.join(5)
    assert received[0]["token"] == "one-use-test-token"
    assert bool(launched) == (mode == "success")
    with RunStore.open(tmp_path / ".simulatte") as store:
        selected = EnvironmentPool(tmp_path, store).selected
        assert (selected is not None) == (mode == "success")


def test_probe_and_invalid_invocation(tmp_path, monkeypatch, capsys):
    from simulatte.studio import server

    monkeypatch.setattr(server, "assets", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["bootstrap", "--probe"])
    bootstrap.main()
    assert json.loads(capsys.readouterr().out)["protocol"] == 1
    monkeypatch.setattr(sys, "argv", ["bootstrap", "bad"])
    with pytest.raises(ValueError, match="Internal"):
        bootstrap.main()


@pytest.mark.parametrize("result", ["error", "bad-json", "oserror", "timeout", "valid"])
def test_probe_failures_are_actionable(monkeypatch, result):
    def run(*args, **kwargs):
        assert args[0][1] == "-I"
        if result == "oserror":
            raise OSError("missing interpreter")
        if result == "timeout":
            raise subprocess.TimeoutExpired("probe", 30)
        return SimpleNamespace(
            returncode=int(result == "error"),
            stderr="missing Studio",
            stdout="bad" if result == "bad-json" else json.dumps(CAPABILITY),
        )

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    if result == "valid":
        assert bootstrap.validate_runtime(Path(sys.executable)) == CAPABILITY
    else:
        with pytest.raises(ValueError, match="Studio|runtime"):
            bootstrap.validate_runtime(Path(sys.executable))


def test_selection_and_restart_preserve_runtime_identity(tmp_path, monkeypatch):
    from simulatte.projects.service import ProjectService
    from simulatte.studio import command

    target = StudioTarget(tmp_path, None)
    assert bootstrap.selected_interpreter(target) is None
    with RunStore.open(tmp_path / ".simulatte") as store:
        assert bootstrap.selected_interpreter(target) is None
        pool = EnvironmentPool(tmp_path, store)
        generation = pool.use_existing(Path(sys.executable))
        pool.activate(generation.id)
    assert bootstrap.selected_interpreter(target) == Path(sys.executable)
    launches = []
    monkeypatch.setattr(bootstrap, "validate_runtime", lambda path: CAPABILITY)
    monkeypatch.setattr(bootstrap, "handoff", lambda *a, **kw: launches.append((a, kw)))

    def serve(service, **kwargs):
        service.restart_generation = generation.id
        service.restart_interpreter = generation.interpreter

    monkeypatch.setattr(command, "serve_project", serve)
    bootstrap.launch_project(target, port=0, no_browser=True)
    assert launches[-1][1]["generation"] == generation.id
    # A new launcher cannot take the writer if the original runtime is elsewhere.
    monkeypatch.setattr(bootstrap, "selected_interpreter", lambda target: tmp_path / "elsewhere")
    bootstrap.launch_project(target, port=0, no_browser=True)
    assert launches[-1][0][0] == tmp_path / "elsewhere"
    # Reopening the original selected runtime remains a data-only operation.
    service = ProjectService.open(target)
    try:
        assert service.coordinator is None and not service.read_only
    finally:
        service.close()
