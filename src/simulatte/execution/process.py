"""One-use process containment with a separate parent-liveness guardian.

Only standard-library imports: this file is also executed directly with -I -S.
A private authenticated loopback socket carries guardian status. User stdout and
stderr are diagnostic pipes, never protocol channels. On POSIX the guardian
owns a new child session and kills its ordinary descendants on every exit path;
deliberately detached sessions are outside this non-sandbox contract.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time


class ContainedProcess:
    def __init__(self, guardian: subprocess.Popen[bytes], channel: socket.socket, pid: int):
        self._guardian = guardian
        self._channel = channel
        self._buffer = bytearray()
        self.pid = pid
        self.returncode: int | None = None
        assert guardian.stdout is not None and guardian.stderr is not None
        self.stdout = guardian.stdout
        self.stderr = guardian.stderr
        self._closed = False

    @classmethod
    def start(
        cls, argv: list[str], *, cwd: str | Path | None = None, env: dict[str, str] | None = None
    ) -> ContainedProcess:
        if os.name not in ("posix", "nt"):
            raise OSError("Supported process containment is unavailable on this platform")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        token = secrets.token_hex(32)
        guardian = None
        channel = None
        try:
            guardian = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(Path(__file__).resolve()),
                    "--guardian",
                    str(listener.getsockname()[1]),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name == "posix",
                bufsize=0,
            )
            config = (
                json.dumps(
                    {"argv": argv, "cwd": str(cwd) if cwd is not None else None, "env": env, "token": token}
                ).encode()
                + b"\n"
            )
            if len(config) > 256 << 10:
                raise ValueError("Process configuration exceeds limit")
            assert guardian.stdin is not None
            guardian.stdin.write(config)
            channel, _ = listener.accept()
            channel.settimeout(10)
            instance = cls(guardian, channel, 0)
            hello = instance._receive(10)
            if hello.get("token") != token or hello.get("type") != "started":
                raise OSError("Cannot launch contained child: " + str(hello.get("error", "invalid guardian")))
            if type(hello.get("pid")) is not int or hello["pid"] <= 1:
                raise OSError("Cannot launch contained child: invalid child pid")
            instance.pid = hello["pid"]
            return instance
        except BaseException:
            if channel is not None:
                channel.close()
            if guardian is not None:
                if guardian.stdin is not None:
                    guardian.stdin.close()
                try:
                    guardian.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    guardian.kill()
                    guardian.wait()
                for pipe in (guardian.stdout, guardian.stderr):
                    if pipe is not None:
                        pipe.close()
            raise
        finally:
            listener.close()

    def _receive(self, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Guardian response timeout")
            self._channel.settimeout(remaining)
            data = self._channel.recv(4096)
            if not data:
                raise OSError("Process guardian lost")
            self._buffer.extend(data)
            if len(self._buffer) > 8192:
                raise OSError("Invalid guardian status")
        raw, _, rest = self._buffer.partition(b"\n")
        self._buffer[:] = rest
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise OSError("Invalid guardian status")
        return result

    def _command(self, command: bytes) -> None:
        if self.returncode is not None or self._closed:
            return
        try:
            assert self._guardian.stdin is not None
            self._guardian.stdin.write(command)
        except BrokenPipeError:
            pass

    def terminate(self) -> None:
        self._command(b"T")

    def kill(self) -> None:
        self._command(b"K")

    def wait(self, timeout: float = 10) -> int:
        deadline = time.monotonic() + timeout
        if self.returncode is None:
            result = self._receive(timeout)
            if result.get("type") != "exit" or type(result.get("code")) is not int:
                # Keep the guardian's own error text: it names the failing cleanup call.
                raise OSError("Invalid child exit status: " + str(result.get("error", result))[:2048])
            self.returncode = result["code"]
        try:
            self._guardian.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("Guardian exit timeout") from error
        return self.returncode

    def close(self) -> None:
        if self._closed:
            return
        # EOF is a separate nonblocking liveness path, including after a crashed
        # supervisor. The guardian kills the process group rather than waiting on
        # inherited stdout/stderr descriptors.
        assert self._guardian.stdin is not None
        self._guardian.stdin.close()
        try:
            self.wait(timeout=5)
        except (OSError, TimeoutError):
            # The independent guardian is the normal owner. If it itself dies,
            # a known live child group still has a best-effort supervisor backup.
            if os.name == "posix":
                _signal_group(self.pid, signal.SIGKILL)
            if self._guardian.poll() is None:
                self._guardian.kill()
            self._guardian.wait(timeout=1)
            raise
        finally:
            self._closed = True
            self._channel.close()
            self.stdout.close()
            self.stderr.close()


def _signal_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS reports EPERM, not ESRCH, for a group that still exists but
        # has no signalable member: typically a leader that exited but is
        # not reaped yet. A repeated terminate/kill around the child's exit
        # must not crash the guardian; the next poll() reaps the zombie.
        # An unsignalable live descendant would need another uid, which the
        # trusted-Python contract already excludes.
        pass


class PosixGroup(subprocess.Popen):
    def terminate(self):
        _signal_group(self.pid, signal.SIGTERM)

    def kill(self):
        _signal_group(self.pid, signal.SIGKILL)

    def close(self):
        pass


def _launch(argv, cwd, env=None):
    environment = None if env is None else {**os.environ, **env}
    if os.name == "posix":
        return PosixGroup(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True, env=environment
        )
    import importlib.util

    spec = importlib.util.spec_from_file_location("_simulatte_windows_job", Path(__file__).with_name("windows_job.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.JobProcess(argv, cwd, env=environment)


def _guardian(port: int, token: str | None = None, lifeline=None) -> int:
    # The daemon lifeline reader may still be blocked when the child exits.
    # Buffered stdin holds a Python I/O lock and can SIGABRT at finalization;
    # raw file reads have no buffered lock for interpreter shutdown to acquire.
    lifeline = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer) if lifeline is None else lifeline
    channel = socket.create_connection(("127.0.0.1", port), timeout=10)
    child = None
    commands: queue.Queue[bytes] = queue.Queue(maxsize=4)
    lost = threading.Event()

    def send(data):
        channel.sendall(json.dumps(data).encode() + b"\n")

    def read_lifeline():
        while True:
            command = lifeline.read(1)
            if not command:
                lost.set()
                return
            try:
                commands.put_nowait(command)
            except queue.Full:
                # Repeated control cannot starve cleanup or allocate unboundedly.
                lost.set()
                return

    record: dict | None = None
    try:
        # Raw reads are unbuffered, so collect the configuration line in chunks and
        # keep any trailing bytes: one reader owns this stream, including commands
        # that arrive before the lifeline thread starts.
        data = bytearray()
        # read1 returns whatever is available; a buffered read(n) would wait for n bytes.
        read = getattr(lifeline, "read1", lifeline.read)
        while b"\n" not in data and len(data) <= 256 << 10:
            chunk = read(min(65536, (256 << 10) + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        raw, newline, rest = bytes(data).partition(b"\n")
        if len(raw) >= 256 << 10 or not newline:
            raise ValueError("Invalid process configuration")
        for byte in rest:
            commands.put_nowait(bytes((byte,)))
        config = json.loads(raw)
        token = config.get("token", token)
        child = _launch(config["argv"], config["cwd"], config.get("env"))
        threading.Thread(target=read_lifeline, daemon=True).start()
        send({"type": "started", "token": token, "pid": child.pid})
        while child.poll() is None:
            if lost.is_set():
                child.kill()
            try:
                command = commands.get(timeout=0.02)
            except queue.Empty:
                continue
            if command == b"T":
                child.terminate()
            elif command == b"K":
                child.kill()
            else:
                lost.set()
        record = {"type": "exit", "code": child.returncode}
    except Exception as error:
        record = {"type": "error", "token": token, "error": str(error)[:2048]}
    finally:
        # Descendants are reaped on every path, even a successful parent exit:
        # they may hold pipes or continue computation after a model has returned.
        if child is not None:
            try:
                child.kill()
                child.wait()
                child.close()
            except Exception as error:
                # The supervisor is waiting for exactly one record. A cleanup
                # failure is reported in it instead of a traceback that would
                # skip the status socket close and leave the exit code undefined.
                failure = "Descendant cleanup failed: " + str(error)
                record = {"type": "error", "token": token, "error": failure[:2048]}
        if record is not None:
            try:
                send(record)
            except OSError:
                pass
        channel.close()
    return 0 if record is not None and record["type"] == "exit" else 2


if __name__ == "__main__":
    raise SystemExit(_guardian(int(sys.argv[2])))
