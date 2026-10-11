"""Real process trees must be reaped even when a descendant holds output open."""

from __future__ import annotations

import importlib
import importlib.util
import os
import signal
import subprocess
import sys
import time

import pytest


def api():
    assert importlib.util.find_spec("simulatte.execution.process"), "process containment missing"
    return importlib.import_module("simulatte.execution.process")


def alive(pid):
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


def gone(*pids):
    deadline = time.monotonic() + 5
    while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not any(alive(pid) for pid in pids)


@pytest.mark.skipif(os.name != "posix", reason="POSIX group/guardian integration")
@pytest.mark.parametrize("parent_exit", [True, False])
def test_ordinary_descendants_reaped_on_exit_or_cancel(tmp_path, parent_exit):
    m = api()
    pid_file = tmp_path / "pids"
    script = (
        "import subprocess,sys,os,time\nfrom pathlib import Path\n"
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
        f'Path({str(pid_file) + ".tmp"!r}).write_text(str(os.getpid())+" "+str(child.pid))\n'
        f"Path({str(pid_file) + '.tmp'!r}).replace({str(pid_file)!r})\n"
        + ("sys.exit(0)\n" if parent_exit else "time.sleep(60)\n")
    )
    process = m.ContainedProcess.start([sys.executable, "-I", "-S", "-c", script])
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        pids = [int(pid) for pid in pid_file.read_text().split()]
        if not parent_exit:
            process.terminate()
        assert process.wait(timeout=5) == (0 if parent_exit else -signal.SIGTERM)
        gone(*pids)
        assert process.stdout.read() == b""
    finally:
        process.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX lifeline integration")
def test_supervisor_hard_death_cleans_cpu_bound_child(tmp_path):
    m = api()
    pid_file = tmp_path / "pids"
    script = (
        "import sys,time\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(__import__('pathlib').Path(m.__file__).parents[2])!r})\n"
        "from simulatte.execution.process import ContainedProcess\n"
        f'p=ContainedProcess.start([sys.executable,"-I","-S","-c","while True: pass"])\n'
        f"Path({str(pid_file) + '.tmp'!r}).write_text(str(p.pid))\n"
        f"Path({str(pid_file) + '.tmp'!r}).replace({str(pid_file)!r})\n"
        "time.sleep(60)\n"
    )
    supervisor = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        pid = int(pid_file.read_text())
        supervisor.kill()
        supervisor.wait(timeout=5)
        gone(pid)
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait()


def test_spawn_failure_refuses_execution_and_releases_guardian():
    m = api()
    with pytest.raises(OSError, match="launch"):
        m.ContainedProcess.start(["/sp4/no-such-executable"])


@pytest.mark.skipif(os.name != "posix", reason="POSIX guardian control paths")
@pytest.mark.parametrize("command", [b"T", b"K", b"?", b""])
def test_guardian_control_pipe_and_status_cleanup(command):
    import json
    import socket
    import threading

    m = api()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    read_fd, write_fd = os.pipe()
    lifeline = os.fdopen(read_fd, "rb")
    writer = os.fdopen(write_fd, "wb", buffering=0)
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(m._guardian(listener.getsockname()[1], "token", lifeline)))
    thread.start()
    connection, _ = listener.accept()
    stream = connection.makefile("rb")
    try:
        writer.write(
            json.dumps(
                {"argv": [sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)"], "cwd": None}
            ).encode()
            + b"\n"
        )
        started = json.loads(stream.readline())
        assert started["type"] == "started"
        if command:
            writer.write(command)
        else:
            writer.close()
        result = json.loads(stream.readline())
        assert result["code"] == (-signal.SIGTERM if command == b"T" else -signal.SIGKILL)
        thread.join(5)
        assert outcome == [0]
        gone(started["pid"])
    finally:
        writer.close()
        thread.join(5)
        lifeline.close()
        stream.close()
        connection.close()
        listener.close()


@pytest.mark.parametrize(
    "raw", [b"bad\n", b"{}", b"x" * (256 * 1024 + 1), b'{"argv":["/no/such/program"],"cwd":null}\n']
)
def test_guardian_malformed_configuration_is_reported(raw):
    import io
    import json
    import socket
    import threading

    m = api()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(m._guardian(listener.getsockname()[1], "token", io.BytesIO(raw)))
    )
    thread.start()
    connection, _ = listener.accept()
    with connection.makefile("rb") as stream:
        assert json.loads(stream.readline())["type"] == "error"
    thread.join(5)
    assert result == [2]
    connection.close()
    listener.close()


@pytest.mark.skipif(os.name != "posix", reason="Guardian failure backup uses POSIX group ids")
def test_guardian_failure_still_cleans_known_child_group():
    m = api()
    process = m.ContainedProcess.start([sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)"])
    process._guardian.kill()
    process._guardian.wait(timeout=5)
    try:
        with pytest.raises(OSError, match="guardian lost"):
            process.close()
        gone(process.pid)
    finally:
        # Bound a failing test too; never leave a process tree behind.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize("raw", [b"[]\n", b"x" * 9000, b""])
def test_guardian_status_parser_refuses_corruption(raw):
    import io
    import socket
    from types import SimpleNamespace

    m = api()
    left, right = socket.socketpair()
    guardian = SimpleNamespace(stdout=io.BytesIO(), stderr=io.BytesIO())
    process = m.ContainedProcess(guardian, left, 123)
    try:
        import threading

        def produce():
            right.sendall(raw)
            right.shutdown(socket.SHUT_WR)

        sender = threading.Thread(target=produce)
        sender.start()
        with pytest.raises((OSError, ValueError)):
            process._receive(0.2)
        sender.join(1)
    finally:
        left.close()
        right.close()


def test_guardian_status_deadline_and_invalid_exit():
    import io
    import socket
    from types import SimpleNamespace

    m = api()
    left, right = socket.socketpair()
    process = m.ContainedProcess(SimpleNamespace(stdout=io.BytesIO(), stderr=io.BytesIO()), left, 123)
    try:
        with pytest.raises(TimeoutError):
            process._receive(0)
        right.sendall(b'{"type":"exit","code":true}\n')
        with pytest.raises(OSError, match="exit status"):
            process.wait(0.2)
    finally:
        left.close()
        right.close()


def test_unsupported_platform_and_missing_guardian_interpreter(monkeypatch):
    from types import SimpleNamespace

    m = api()
    with monkeypatch.context() as patch:
        patch.setattr(m, "os", SimpleNamespace(name="unsupported"))
        with pytest.raises(OSError, match="unavailable"):
            m.ContainedProcess.start(["anything"])
    with monkeypatch.context() as patch:
        patch.setattr(m, "sys", SimpleNamespace(executable="/sp4/no-interpreter"))
        with pytest.raises(OSError):
            m.ContainedProcess.start(["anything"])


def test_oversize_process_configuration_never_launches_user_code():
    m = api()
    with pytest.raises(ValueError, match="limit"):
        m.ContainedProcess.start(["x" * (256 * 1024)])
