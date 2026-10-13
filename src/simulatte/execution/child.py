"""One-use isolated inspection/execution child and its private controls."""

from __future__ import annotations

import os
import errno
import math
import threading
import traceback

from simulatte.execution.control import Control, Cancelled, AttemptTimeout
from simulatte.execution.schema import AttemptKey
from simulatte.execution.transport import FramedChannel


def key_of(message):
    value = message.envelope
    return AttemptKey(value["request_id"], value["attempt_id"], value["generation"])


def serve(config, connection) -> int:
    stream = connection.makefile("rwb", buffering=0)
    channel = FramedChannel(stream, stream)
    channel.send("hello", {"pid": os.getpid(), "protocol": [1, 0]})
    control = Control()
    key = None
    sender = None
    stopped = threading.Event()
    try:
        if channel.receive(10).type != "hello":
            raise ValueError("Missing supervisor hello")
        command = channel.receive(10)
        key = key_of(command)
        if command.type not in ("inspect", "run"):
            raise ValueError("Expected one inspect or run command")
        timeout = command.body.get("timeout")
        if timeout is not None:
            if (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise ValueError("Invalid timeout")
            control.deadline = control.clock() + float(timeout)

        def controls():
            while not stopped.is_set():
                try:
                    message = channel.receive(0.1)
                    if key_of(message) != key:
                        continue
                    if message.type == "cancel":
                        control.cancel()
                    elif message.type == "ready_ack":
                        control.ack_ready()
                    elif message.type == "ack":
                        if sender is not None:
                            sender.ack(dict(message.body))
                    else:
                        control.fail(ValueError("Unsupported private control message"))
                except TimeoutError:
                    continue
                except ValueError as error:
                    control.fail(error)
                    return
                except OSError:
                    control.cancel()
                    return

        def start_sender():
            nonlocal sender
            from pathlib import Path
            from simulatte.execution.spool import Spool
            from simulatte.execution.transfer import SegmentSender

            spool = Spool(Path(config["attempt_dir"]) / "spool", control=control)
            sender = SegmentSender(spool, send)
            sender.start()
            return spool

        def send(kind, body, **kwargs):
            channel.send(kind, body, key=key, **kwargs)

        threading.Thread(target=controls, daemon=True).start()
        control.check()
        if command.type == "inspect":
            from simulatte.execution.bootstrap import inspect_configuration

            control.phase = "inspecting"
            inspection = None
            if "inspection_ref" in command.body:
                from simulatte.execution.references import read_document
                from simulatte.execution.schema import InspectionRequest, inspection_id
                from simulatte.execution.runtime import verify_runtime

                inspection = InspectionRequest.from_dict(read_document(command.body["inspection_ref"], "request"))
                if inspection_id(inspection) != key.request_id or inspection.model != config["reference"]:
                    raise ValueError("Inspection request does not match assignment")
                verify_runtime(config, inspection.runtime)
            metadata = inspect_configuration(config)
            if inspection is not None:
                verify_runtime(config, inspection.runtime)
            control.check()
            if config.get("metadata_artifacts", False):
                from simulatte.execution.driver import metadata_segment

                spool = start_sender()
                reference = metadata_segment(spool, "model-info", metadata)
                assert sender is not None
                sender.finish()
                sender.wait()
                channel.send("model_info", {"metadata_ref": reference, "pid": os.getpid()}, key=key)
            else:
                channel.send("model_info", {"metadata": metadata, "pid": os.getpid()}, key=key)
        else:
            from simulatte.execution.driver import execute

            spool = start_sender()
            assert sender is not None
            if "request_ref" in command.body:
                from simulatte.execution.references import read_document

                request = read_document(command.body["request_ref"], "request")
            else:
                request = command.body["request"]
            proposal = execute(config, request, key, control, spool, send)
            sender.finish()
            sender.wait()
            channel.send("result", {"proposal": proposal, "pid": os.getpid()}, key=key)
        channel.drain(5)
        return 0
    except BaseException as error:
        kind = "cancelled" if isinstance(error, Cancelled) else "error"
        code = "cancelled" if kind == "cancelled" else "timeout" if isinstance(error, AttemptTimeout) else "child_error"
        if getattr(error, "code", None) == "runtime_changed":  # runtime.RuntimeChanged
            code = "runtime_changed"
        if isinstance(error, OSError) and (
            error.errno in (errno.ENOSPC, errno.EDQUOT) or getattr(error, "code", None) == "disk_full"
        ):
            code = "disk_full"
        try:
            channel.send(
                kind,
                {
                    "phase": control.phase,
                    "code": code,
                    "message": str(error)[:4096],
                    "traceback": "".join(traceback.format_exception(error))
                    .encode("utf-8")[-65536:]
                    .decode("utf-8", errors="ignore"),
                },
                key=key,
            )
            channel.drain(1)
        except (OSError, TimeoutError, ValueError, BufferError):
            pass
        return 2
    finally:
        control.cancel()
        if sender is not None:
            try:
                sender.wait(1)
            except BaseException:
                pass
        stopped.set()
        try:
            connection.shutdown(2)
        except OSError:
            pass
        channel.close()
        stream.close()
        connection.close()
