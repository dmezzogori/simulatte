"""Coordinator side of remote staging and provisioning (protocol 1.1, design section 9).

``stage_blob`` ships one content-addressed file as ``stage`` plus raw ``stage_fragment`` frames of
at most 1 MiB, never more than 16 MiB beyond the worker's durable extent (its ``ack``), and returns
once the worker answers ``staged``. ``provision`` asks the worker for the uv environment of a
provisioning spec. Both run on the slot actor's thread before ``run``; heartbeats keep flowing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import time
from typing import Any

from simulatte.execution.protocol import MAX_FRAGMENT
from simulatte.execution.schema import AttemptKey, _plain

WINDOW = 16 << 20
# Beyond the worker's own provisioning timeout: a live but silent worker ends the step.
ANSWER_GRACE = 30.0


class RemoteStepError(RuntimeError):
    """The worker refused or failed a step with a known ``code``; the attempt is ``failed``."""

    def __init__(self, code: str, details: Mapping[str, Any]):
        self.code, self.details = code, {**_plain(details), "code": code}
        super().__init__(self.details.get("message", code))


def _none():
    return None


def _await(transport, key: AttemptKey, *, check: Callable[[], None], notify, deadline: float | None = None):
    """The next message for ``key``: heartbeats are reported and skipped, ``error`` raises.

    Loss of the channel or of heartbeats raises ``ConnectionError``, which callers treat as
    ``lost``; local file errors stay ordinary ``OSError``.
    """
    while True:
        check()
        if deadline is not None and time.monotonic() >= deadline:
            raise RemoteStepError("provisioning_failed", {"reason": "timeout", "message": "Worker did not answer"})
        try:
            message = transport.receive(0.1)
        except TimeoutError as error:
            if time.monotonic() - transport.channel.last_heartbeat >= 5:
                raise ConnectionError("Worker heartbeat lost") from error
            continue
        except OSError as error:
            raise ConnectionError(f"Worker transport closed: {error}") from error
        identity = (message.envelope["request_id"], message.envelope["attempt_id"], message.envelope["generation"])
        mine = identity == (key.request_id, key.attempt_id, key.generation)
        if message.type == "heartbeat":
            if mine:
                notify("heartbeat", message.body)
            continue  # Heartbeats are liveness only, whichever step they describe.
        if not mine:
            raise ValueError("Worker message belongs to another assignment")
        if message.type == "diagnostics":
            notify("diagnostics", message.body)
            continue
        if message.type == "error":
            raise RemoteStepError(str(message.body.get("code", "remote_error")), message.body)
        return message


def _send(transport, kind, body, key, payload=b""):
    try:
        transport.send(kind, body, key=key, payload=payload)
    except BufferError:
        raise
    except OSError as error:
        raise ConnectionError(f"Worker transport closed: {error}") from error


def stage_blob(
    transport,
    kind: str,
    path: Path,
    sha256: str,
    key: AttemptKey,
    *,
    check: Callable[[], None] = _none,
    notify=lambda kind, body: None,
) -> bool:
    """Make ``path`` available on the worker as ``<cache>/blobs/<sha256>``; ``True`` when it was cached."""
    length = Path(path).stat().st_size
    _send(transport, "stage", {"kind": kind, "sha256": sha256, "length": length}, key)
    message = _await(transport, key, check=check, notify=notify)
    if message.type == "staged":
        if message.body["sha256"] != sha256 or message.body["cached"] is not True:
            raise ValueError("Unexpected staging answer")
        return True
    if message.type != "ack" or message.body["sha256"] != sha256 or message.body["received"] != 0:
        raise ValueError("Unexpected staging answer")
    sent = received = 0
    with Path(path).open("rb") as file:
        while received < length:
            while sent < length and sent - received + min(MAX_FRAGMENT, length - sent) <= WINDOW:
                file.seek(sent)
                payload = file.read(min(MAX_FRAGMENT, length - sent))
                if len(payload) != min(MAX_FRAGMENT, length - sent):
                    raise ValueError("Staged file changed while sending")
                body = {"sha256": sha256, "fragment_offset": sent, "payload_bytes": len(payload)}
                try:
                    _send(transport, "stage_fragment", body, key, payload)
                except BufferError:
                    break  # The data lane is full: credit returns as the worker acknowledges.
                sent += len(payload)
            message = _await(transport, key, check=check, notify=notify)
            extent = message.body.get("received")
            if (
                message.type != "ack"
                or message.body["sha256"] != sha256
                or type(extent) is not int
                or not received < extent <= sent
            ):
                raise ValueError("Unexpected staging acknowledgement")
            received = extent
    message = _await(transport, key, check=check, notify=notify)
    if message.type != "staged" or message.body["sha256"] != sha256 or message.body["cached"] is not False:
        raise ValueError("Unexpected staging answer")
    return False


def provision(
    transport,
    spec: Mapping[str, Any],
    source_sha256: str,
    key: AttemptKey,
    *,
    timeout: float,
    check: Callable[[], None] = _none,
    notify=lambda kind, body: None,
) -> dict[str, Any]:
    """The worker's ``provisioned {spec_hash, evidence_sha256, python, site_packages}`` answer."""
    body = {"spec": _plain(spec), "source_sha256": source_sha256, "timeout": timeout}
    _send(transport, "provision", body, key)
    deadline = time.monotonic() + timeout + ANSWER_GRACE
    message = _await(transport, key, check=check, notify=notify, deadline=deadline)
    if message.type != "provisioned":
        raise ValueError("Unexpected provisioning answer")
    return _plain(message.body)
