"""Control and diagnostics must remain responsive independently of model work."""

from __future__ import annotations

import importlib
import importlib.util
import threading

import pytest


def api():
    assert importlib.util.find_spec("simulatte.execution.supervisor"), "supervisor missing"
    return importlib.import_module("simulatte.execution.supervisor")


def test_ready_barrier_cancel_and_deadline_do_not_advance_work():
    m = api()
    control = m.Control()
    completed = threading.Event()

    def waiting():
        with pytest.raises(m.Cancelled):
            control.wait_ready()
        completed.set()

    thread = threading.Thread(target=waiting)
    thread.start()
    control.cancel()
    assert completed.wait(1)
    thread.join()
    ready = m.Control()
    ready.ack_ready()
    ready.wait_ready()
    clock = [1.0]
    timed = m.Control(deadline=2, clock=lambda: clock[0])
    timed.check()
    clock[0] = 2
    with pytest.raises(m.AttemptTimeout):
        timed.check()


def test_diagnostic_tails_and_rate_limit_are_bounded():
    m = api()
    clock = [0.0]
    diagnostics = m.Diagnostics(clock=lambda: clock[0])
    diagnostics.append("stdout", b"x" * (2 << 20))
    diagnostics.append("stderr", b"last")
    first = diagnostics.take()
    assert len(first["stdout"].encode()) <= 65536
    assert len(diagnostics.tail("stdout")) == 1 << 20
    assert diagnostics.take()["stdout"] == ""
    clock[0] += 1
    diagnostics.append("stdout", b"next")
    second = diagnostics.take()
    assert second["stdout"].endswith("next")
    assert second["dropped_bytes"] > 0
    assert diagnostics.tail("stderr") == b"last"


def test_progress_stall_is_separate_from_transport_liveness():
    m = api()
    clock = [0.0]
    state = m.Progress(clock=lambda: clock[0])
    state.update(phase="running", simulation_time=1, event_count=2)
    clock[0] = 11
    assert state.heartbeat()["stalled"]
    state.update(phase="running", simulation_time=2, event_count=3)
    assert not state.heartbeat()["stalled"]


def test_fresh_inspections_native_output_and_worker_heartbeat(tmp_path):
    import sysconfig
    from pathlib import Path
    from simulatte.execution.schema import AttemptKey
    from simulatte.execution.transport import LocalTransport
    from simulatte.execution.snapshot import freeze_runtime

    runtime = Path(__file__).resolve().parents[2] / "src" / "simulatte"
    project = tmp_path / "project"
    snapshot = tmp_path / "snapshot"
    project.mkdir()
    snapshot.mkdir()
    (snapshot / "plant.py").write_text(
        "import os\nfrom dataclasses import dataclass\nimport simulatte\n"
        'os.write(1,b"SIMWORK\\0"*10000)\nos.write(2,b"noise"*10000)\n'
        "@dataclass(frozen=True)\nclass P: n:int=1\n"
        "@simulatte.model(params=P,horizon=1)\ndef build(env,layout,params): pass\n"
    )
    frozen = freeze_runtime(runtime, project)
    worker = LocalTransport.start(frozen.root, external_roots=[sysconfig.get_path("purelib")])
    pids = []
    try:
        assert worker.receive(5).type == "hello"
        for generation in (1, 2):
            key = AttemptKey("a" * 64, str(generation) * 32, generation)
            worker.send(
                "inspect",
                {
                    "config": {
                        "project_root": str(project),
                        "snapshot_roots": [str(snapshot)],
                        "external_roots": [str(frozen.root), sysconfig.get_path("purelib")],
                        "reference": "plant.py",
                    }
                },
                key=key,
            )
            while True:
                message = worker.receive(5)
                assert message.type != "error", str(message.body)
                if message.type == "model_info":
                    pids.append(message.body["pid"])
                    assert message.body["metadata"]["selected"] == "build"
                    break
        assert pids[0] != pids[1]
    finally:
        worker.close()


@pytest.mark.parametrize("behavior", ["exception", "exit", "cpu", "native_flood", "timeout"])
def test_import_failures_and_uncooperative_cancellation(tmp_path, behavior):
    import sysconfig
    from pathlib import Path
    import time
    from simulatte.execution.schema import AttemptKey
    from tests.execution._worker_harness import WorkerHarness
    from simulatte.execution.snapshot import freeze_runtime

    runtime = Path(__file__).resolve().parents[2] / "src" / "simulatte"
    project, snapshot = tmp_path / "project", tmp_path / "snapshot"
    project.mkdir()
    snapshot.mkdir()
    bodies = {
        "exception": 'raise RuntimeError("broken import")',
        "exit": "import os; os._exit(7)",
        "cpu": "while True: pass",
        "native_flood": 'import os\nwhile True: os.write(1,b"x"*65536)',
        "timeout": "while True: pass",
    }
    (snapshot / "plant.py").write_text(bodies[behavior])
    frozen = freeze_runtime(runtime, project)
    worker = WorkerHarness()
    key = AttemptKey("b" * 64, "c" * 32, 1)
    heartbeats = 0
    cancelled_at = None
    try:
        worker.receive(5)
        command = {
            "config": {
                "project_root": str(project),
                "snapshot_roots": [str(snapshot)],
                "external_roots": [str(frozen.root), sysconfig.get_path("purelib")],
                "reference": "plant.py",
            }
        }
        if behavior == "timeout":
            command["timeout"] = 0.25
        worker.send("inspect", command, key=key)
        deadline = time.monotonic() + 9
        while time.monotonic() < deadline:
            message = worker.receive(5)
            if message.type == "heartbeat":
                heartbeats += 1
                if behavior in ("cpu", "native_flood") and heartbeats == 2:
                    cancelled_at = time.monotonic()
                    worker.send("cancel", {}, key=key)
            if message.type in ("cancelled", "error"):
                if behavior in ("cpu", "native_flood"):
                    assert message.type == "cancelled"
                    assert cancelled_at is not None and time.monotonic() - cancelled_at < 5
                else:
                    assert message.type == "error"
                    assert (
                        message.body["code"]
                        == {"exception": "child_error", "exit": "child_protocol_lost", "timeout": "timeout"}[behavior]
                    )
                break
        else:
            pytest.fail("Worker did not terminate the attempt")
    finally:
        worker.close()
