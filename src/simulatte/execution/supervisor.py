"""Trusted local supervision; model imports only happen in execution children."""

from __future__ import annotations

import json
import math
import platform
from pathlib import Path
import secrets
import socket
import struct
import sys
import threading
import tempfile
import time

from simulatte.execution.control import Control, Cancelled, AttemptTimeout
from simulatte.execution.diagnostics import Diagnostics, Progress
from simulatte.execution.process import ContainedProcess
from simulatte.execution.protocol import MAX_FRAME, MAX_ENVELOPE, MAX_FRAGMENT
from simulatte.provenance import _simulatte_version
from simulatte.execution.schema import AttemptKey, _plain
from simulatte.execution.transport import FramedChannel

__all__ = ["Control", "Cancelled", "AttemptTimeout", "Diagnostics", "Progress", "Supervisor"]


_CAPABILITIES = frozenset(
    {
        "fresh-process",
        "attempt-fencing",
        "bounded-framing",
        "bounded-diagnostics",
        "ordinary-descendant-cleanup",
        "ready-barrier",
    }
)


def worker_hello():
    return {
        "protocol": [1, 0],
        "platform": sys.platform,
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "simulatte_version": _simulatte_version(),
        },
        "capabilities": sorted(_CAPABILITIES),
        "limits": {"frame_bytes": MAX_FRAME, "envelope_bytes": MAX_ENVELOPE, "fragment_bytes": MAX_FRAGMENT},
        "cleanup": "job" if sys.platform == "win32" else "guardian-process-group",
        "warm_spares": 0,
        "slots": 1,
    }


def _key(message):
    value = message.envelope
    return AttemptKey(value["request_id"], value["attempt_id"], value["generation"])


class ChildProcess:
    def __init__(self, command):
        self.key = _key(command)
        self.started = time.monotonic()
        self.cancel_at = None
        self.terminated = False
        self.killed = False
        self.hello = False
        self.proposal = None
        self.pending = None
        self.exit_code = None
        self.diagnostics = Diagnostics()
        self.reported_drops = 0
        self.progress = Progress()
        self.process = None
        self.channel = None
        self.connection = None
        self.stream = None
        self.pumps = []
        self.attempt_dir = None
        timeout = command.body.get("timeout")
        if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("Attempt timeout must be positive and finite")
        self.deadline = self.started + timeout if timeout is not None else None
        self.timeout = False
        self.failure = None
        self.progress.update(phase="inspecting" if command.type == "inspect" else "preparing")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        token = secrets.token_hex(32)
        try:
            self.attempt_dir = tempfile.TemporaryDirectory(prefix="simulatte-attempt-")
            configuration = _plain(command.body["config"])
            configuration["attempt_dir"] = self.attempt_dir.name
            config = json.dumps(configuration, allow_nan=False).encode()
            if len(config) > 256 << 10:
                raise ValueError("Child configuration exceeds limit")
            self.process = ContainedProcess.start(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(Path(__file__).with_name("bootstrap.py")),
                    "--child",
                    str(listener.getsockname()[1]),
                ],
                env={"SIMULATTE_CHILD_TOKEN": token},
            )
            for name in ("stdout", "stderr"):
                thread = threading.Thread(target=self._pump, args=(name, getattr(self.process, name)), daemon=True)
                thread.start()
                self.pumps.append(thread)
            self.connection, _ = listener.accept()
            self.connection.settimeout(10)
            received = bytearray()
            while len(received) < 65:
                part = self.connection.recv(65 - len(received))
                if not part:
                    raise ValueError("Child disconnected before authentication")
                received.extend(part)
            if not secrets.compare_digest(bytes(received), token.encode() + b"\n"):
                raise ValueError("Invalid child channel token")
            self.connection.sendall(struct.pack(">I", len(config)) + config)
            self.connection.settimeout(None)
            self.stream = self.connection.makefile("rwb", buffering=0)
            self.channel = FramedChannel(self.stream, self.stream)
            self.channel.send("hello", {"protocol": [1, 0]})
            self.channel.send(command.type, dict(command.body), key=self.key)
        except BaseException:
            self.close()
            raise
        finally:
            listener.close()

    def _pump(self, name, pipe):
        try:
            while data := pipe.read(65536):
                self.diagnostics.append(name, data)
        except (OSError, ValueError):
            pass

    def cancel(self, *, timeout=False, failure=None):
        if self.cancel_at is None:
            self.cancel_at = time.monotonic()
            self.timeout = timeout
            self.failure = failure
            if self.channel is not None:
                try:
                    self.channel.send("cancel", {}, key=self.key)
                except (OSError, BufferError):
                    pass

    def escalate(self):
        now = time.monotonic()
        if self.deadline is not None and now >= self.deadline:
            self.cancel(timeout=True)
        if not self.hello and now - self.started >= 10:
            self.cancel(timeout=True)
        if self.cancel_at is not None and self.process is not None:
            if now - self.cancel_at >= 2 and not self.terminated:
                self.process.terminate()
                self.terminated = True
            if now - self.cancel_at >= 3 and not self.killed:
                self.process.kill()
                self.killed = True

    def close(self):
        if self.process is not None:
            self.process.close()
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.channel is not None:
            self.channel.close()
        if self.stream is not None:
            self.stream.close()
        if self.connection is not None:
            self.connection.close()
        for thread in self.pumps:
            thread.join(timeout=0.2)
        if self.attempt_dir is not None:
            self.attempt_dir.cleanup()


class Supervisor:
    def __init__(self, channel=None):
        self.channel = channel
        self.child = None
        self._last_diagnostic = 0.0

    def _command(self, message):
        assert self.channel is not None
        if message.type == "hello":
            required = message.body.get("required_capabilities", ())
            if not isinstance(required, (list, tuple)) or any(not isinstance(item, str) for item in required):
                raise ValueError("Invalid required capabilities")
            if set(required) - _CAPABILITIES:
                raise ValueError("Unsupported required worker capabilities")
            return
        if message.type in ("inspect", "run"):
            key = _key(message)
            if self.child is not None:
                self.channel.send(
                    "error", {"code": "worker_busy", "message": "One local execution slot is active"}, key=key
                )
                return
            try:
                self.child = ChildProcess(message)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.channel.send("error", {"code": "preparation_failed", "message": str(error)[:4096]}, key=key)
        elif message.type in ("cancel", "ready_ack", "ack"):
            if self.child is None or _key(message) != self.child.key:
                return
            if message.type == "cancel":
                self.child.cancel()
            else:
                assert self.child.channel is not None
                self.child.channel.send(message.type, dict(message.body), key=self.child.key)
        else:
            raise ValueError("Unsupported supervisor command")

    def _poll_child(self):
        child = self.child
        if child is None:
            return
        assert self.channel is not None and child.channel is not None and child.process is not None
        child.escalate()
        if child.cancel_at is not None:
            child.pending = None
        channel_lost = False
        for _ in range(32):
            if child.pending is not None:
                message = child.pending
            else:
                try:
                    message = child.channel.receive(0.001)
                except TimeoutError:
                    break
                except OSError:
                    channel_lost = True
                    break
            if message.type == "hello" and not child.hello:
                child.hello = True
                continue
            if _key(message) != child.key:
                raise ValueError("Child attempt fence mismatch")
            if message.type in ("model_info", "result", "error", "cancelled"):
                if child.proposal is not None:
                    raise ValueError("Duplicate child terminal proposal")
                child.proposal = message
            elif message.type == "progress":
                child.progress.update(**dict(message.body))
            elif message.type in ("ready", "kpi", "trace_chunk", "artifact_fragment"):
                if child.cancel_at is None:
                    try:
                        self.channel.send(message.type, dict(message.body), key=child.key, payload=message.payload)
                    except BufferError:
                        child.pending = message
                        break
                child.pending = None
            else:
                raise ValueError("Invalid private child message")
        if channel_lost and child.proposal is None:
            child.cancel(failure="child_protocol_lost")
        try:
            child.exit_code = child.process.wait(timeout=0.001)
        except TimeoutError:
            pass
        if time.monotonic() - self._last_diagnostic >= 0.2:
            report = child.diagnostics.take()
            if report["stdout"] or report["stderr"] or report["dropped_bytes"] != child.reported_drops:
                child.reported_drops = report["dropped_bytes"]
                self.channel.send("diagnostics", report, key=child.key)
            self._last_diagnostic = time.monotonic()
        # A priority terminal proposal can overtake bulk messages. Drain private
        # EOF before releasing the slot; otherwise the final poll batch is lost.
        if child.exit_code is not None and channel_lost and child.pending is None:
            child.close()
            self.child = None
            if child.cancel_at is not None:
                kind = "error" if child.timeout or child.failure else "cancelled"
                body = {
                    "code": "timeout" if child.timeout else child.failure or "cancelled",
                    "message": "Attempt stopped",
                }
            elif child.proposal is not None and (child.exit_code == 0 or child.proposal.type == "error"):
                kind, body = child.proposal.type, dict(child.proposal.body)
            else:
                kind, body = "error", {"code": "child_crashed", "message": f"Child exited with {child.exit_code}"}
            self.channel.send(kind, body, key=child.key)

    def run_stdio(self) -> int:
        if self.channel is None:
            self.channel = FramedChannel(
                getattr(sys.stdin.buffer, "raw", sys.stdin.buffer),
                getattr(sys.stdout.buffer, "raw", sys.stdout.buffer),
            )
        self.channel.send("hello", worker_hello())
        # Heartbeats live outside the event loop as child creation can involve OS
        # work. No child Python execution can hold this interpreter's GIL.
        stopped = threading.Event()

        def heartbeat():
            assert self.channel is not None
            while not stopped.wait(1):
                child = self.child
                try:
                    self.channel.send(
                        "heartbeat",
                        child.progress.heartbeat() if child else {"phase": "idle"},
                        key=child.key if child else None,
                    )
                except (OSError, BufferError):
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            while True:
                try:
                    message = self.channel.receive(0.02)
                    if message.type == "shutdown":
                        break
                    self._command(message)
                except TimeoutError:
                    pass
                self._poll_child()
            return 0
        except (OSError, ValueError, BufferError) as error:
            try:
                self.channel.send(
                    "error",
                    {"code": "worker_protocol_error", "message": str(error)[:4096]},
                    key=self.child.key if self.child else None,
                )
            except (OSError, ValueError, BufferError):
                pass
            return 2
        finally:
            stopped.set()
            if self.child is not None:
                self.child.close()
                self.child = None
            try:
                self.channel.send("bye", {})
                self.channel.drain(0.2)
            except (OSError, TimeoutError, BufferError):
                pass
            self.channel.close()
            thread.join(timeout=0.2)
        if self.attempt_dir is not None:
            self.attempt_dir.cleanup()
