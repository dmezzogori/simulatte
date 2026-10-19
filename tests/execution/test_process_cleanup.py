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
        # A daemon reader must not hold BufferedReader's lock at interpreter exit.
        # The child exit code alone used to hide a guardian SIGABRT and its delay.
        assert process._guardian.returncode == 0
        assert b"Fatal Python error" not in process.stderr.read()
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
@pytest.mark.parametrize(
    ("command", "early"), [(b"T", False), (b"K", False), (b"?", False), (b"", False), (b"K", True)]
)
def test_guardian_control_pipe_and_status_cleanup(command, early):
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
            + (command if early else b"")
        )
        started = json.loads(stream.readline())
        assert started["type"] == "started"
        if early:
            pass
        elif command:
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
    "raw",
    [b"bad\n", b"{}", b"x" * (256 * 1024 + 1), b'{"argv":["/no/such/program"],"cwd":null}\n'],
    ids=["invalid-json", "missing-newline", "oversized", "missing-program"],
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


def test_signal_group_tolerates_unsignalable_group(monkeypatch):
    m = api()

    def unsignalable(pid, signum):
        raise PermissionError(1, "Operation not permitted")

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "killpg", unsignalable)
        m._signal_group(12345, signal.SIGKILL)
    if sys.platform != "darwin":
        return
    # XNU killpg returns EPERM for an existing group whose only member is a zombie.
    sleeper = [sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)"]
    leader = subprocess.Popen(sleeper, start_new_session=True)
    try:
        os.kill(leader.pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            stat = subprocess.run(["ps", "-o", "stat=", "-p", str(leader.pid)], capture_output=True, text=True)
            if stat.stdout.strip().startswith("Z"):
                break
            time.sleep(0.01)
        with pytest.raises(PermissionError):
            os.killpg(leader.pid, signal.SIGTERM)
        m._signal_group(leader.pid, signal.SIGTERM)
    finally:
        leader.wait(timeout=5)
    m._signal_group(leader.pid, signal.SIGKILL)


@pytest.mark.skipif(os.name != "posix", reason="POSIX group/guardian integration")
def test_repeated_terminate_around_child_exit_keeps_guardian_status(tmp_path):
    # AnalysisRunner sends one T from cancel() and another from its loop; the
    # second may reach the guardian after the child exited but before poll()
    # reaped it. The guardian must still report the exit status and exit 0.
    m = api()
    for attempt in range(5):
        marker = tmp_path / f"ready-{attempt}"
        script = (
            "import signal,sys,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\n"
            f"Path({str(marker)!r}).write_text('ready')\n"
            "while True: time.sleep(0.01)\n"
        )
        process = m.ContainedProcess.start([sys.executable, "-I", "-S", "-c", script])
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            process.terminate()
            time.sleep(0.01 * attempt)
            process.terminate()
            # The child exits 0 from its handler, or by SIGTERM when the second
            # signal lands after finalization restored the default disposition.
            # Either way the guardian must report an exit record and exit 0.
            assert process.wait(timeout=5) in (0, -signal.SIGTERM)
            process.close()
            assert process._guardian.returncode == 0
        finally:
            process.close()


def test_guardian_cleanup_failure_is_reported_with_deterministic_exit(monkeypatch):
    import json
    import socket
    import threading

    m = api()

    class Lifeline:
        # Delivers the configuration line, then blocks like an open parent pipe.
        def __init__(self, data):
            self.data, self.closed = data, threading.Event()

        def read(self, size=-1):
            if self.data:
                size = len(self.data) if size is None or size < 0 else size
                chunk, self.data = self.data[:size], self.data[size:]
                return chunk
            self.closed.wait()
            return b""

    launch = m._launch

    def failing_launch(argv, cwd, env=None):
        process = launch(argv, cwd, env)

        def kill():
            raise OSError("injected cleanup failure")

        process.kill = kill
        return process

    monkeypatch.setattr(m, "_launch", failing_launch)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    config = {"argv": [sys.executable, "-I", "-S", "-c", "pass"], "cwd": None}
    lifeline = Lifeline(json.dumps(config).encode() + b"\n")
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(m._guardian(listener.getsockname()[1], "token", lifeline)))
    thread.start()
    connection, _ = listener.accept()
    try:
        with connection.makefile("rb") as stream:
            assert json.loads(stream.readline())["type"] == "started"
            # The child exited 0, but a failing post-exit cleanup is not a clean exit.
            result = json.loads(stream.readline())
            assert result["type"] == "error"
            assert result["error"].startswith("Descendant cleanup failed: ")
            assert "injected cleanup failure" in result["error"]
            # The guardian closes the status socket itself rather than dying with a traceback.
            assert stream.readline() == b""
        thread.join(5)
        assert outcome == [2]
    finally:
        lifeline.closed.set()
        thread.join(5)
        connection.close()
        listener.close()


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
