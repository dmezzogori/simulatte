"""One-use isolated inspection/execution child and its private controls."""

from __future__ import annotations

import os
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
                    elif message.type != "ack":
                        control.fail(ValueError("Unsupported private control message"))
                except TimeoutError:
                    continue
                except ValueError as error:
                    control.fail(error)
                    return
                except OSError:
                    control.cancel()
                    return

        threading.Thread(target=controls, daemon=True).start()
        control.check()
        if command.type == "inspect":
            from simulatte.execution.bootstrap import inspect_configuration

            metadata = inspect_configuration(config)
            control.check()
            channel.send("model_info", {"metadata": metadata, "pid": os.getpid()}, key=key)
        else:
            raise ValueError("Execution driver is not connected yet")
        channel.drain(5)
        return 0
    except BaseException as error:
        kind = "cancelled" if isinstance(error, Cancelled) else "error"
        code = "cancelled" if kind == "cancelled" else "timeout" if isinstance(error, AttemptTimeout) else "child_error"
        try:
            channel.send(
                kind,
                {
                    "phase": "preparing",
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
        stopped.set()
        try:
            connection.shutdown(2)
        except OSError:
            pass
        channel.close()
        stream.close()
        connection.close()
