"""Measure real local-worker control under withheld durable artifact ACKs.

Uses the normal immutable preparation and worker protocol, then deliberately
withholds artifact credit while continuing to drain control frames. Run twice,
with --action cancel and --action timeout. Only temporary owned files are used.
"""

from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
import platform
import resource
import sys
import tempfile
import threading
import time

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.receipts import SegmentReceiver
from simulatte.execution.schema import _plain
from simulatte.execution.snapshot import discover_project, materialize
from simulatte.execution.transport import LocalTransport

MODEL = """import os
from dataclasses import dataclass
from pathlib import Path
from simulatte import model
@dataclass(frozen=True)
class Params: pass
@model(params=Params,horizon=2)
def build(env,layout,params):
    Path(PID_PATH).write_text(str(os.getpid()))
    def work():
        yield env.timeout(1)
        with (env.artifacts_dir / "large.bin").open("wb") as output:
            block = os.urandom(1 << 20)
            for _ in range(320): output.write(block)
    env.process(work())
"""


def quota_report():
    """Exercise both default file and sender-window limits without a simulated clock."""
    from simulatte.execution.control import Cancelled
    from simulatte.execution.spool import Spool
    from simulatte.execution.transfer import SegmentSender

    with tempfile.TemporaryDirectory(prefix="simulatte-quota-") as directory:
        root = Path(directory)
        spool = Spool(root / "files")
        for index in range(1024):
            reservation = spool.reserve(1)
            reservation.write(b"x")
            spool.commit(reservation, stream="trace", offset=index)
        cancelled = threading.Event()

        def reserve():
            try:
                spool.reserve(1)
            except Cancelled:
                cancelled.set()

        thread = threading.Thread(target=reserve)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not spool.stats()["backpressure"] and time.monotonic() < deadline:
                time.sleep(0.01)
            files = spool.stats()
            assert files["backpressure"] and files["files"] == 1024
        finally:
            spool.control.cancel()
            thread.join(2)
        assert cancelled.is_set() and not thread.is_alive()
        spool = Spool(root / "window")
        reservation = spool.reserve(17 << 20)
        for _ in range(17):
            reservation.write(b"x" * (1 << 20))
        spool.commit(reservation, stream="trace", offset=0)
        sender = SegmentSender(spool, lambda *args, **kwargs: None)
        sender.start()
        try:
            deadline = time.monotonic() + 5
            while sender.stats()["in_flight"] < 16 << 20 and time.monotonic() < deadline:
                time.sleep(0.01)
            window = sender.stats()
            assert window["in_flight"] == 16 << 20
        finally:
            spool.control.cancel()
            try:
                sender.wait(2)
            except Cancelled:
                pass
        return {"file_quota": files, "sender_window": window}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("cancel", "timeout", "quotas"), default="cancel")
    args = parser.parse_args()
    if args.action == "quotas":
        print(json.dumps(quota_report(), indent=2, sort_keys=True))
        return
    with tempfile.TemporaryDirectory(prefix="simulatte-backpressure-") as directory:
        root = Path(directory)
        pid_path = root / "child.pid"
        (root / "plant.py").write_text(MODEL.replace("PID_PATH", repr(str(pid_path))))
        (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["plant.py"]\n')
        with Coordinator(discover_project(root / "plant.py", root)) as coordinator:
            prepared = coordinator.prepare(timeout=60)
            bound = coordinator.inputs.bind(prepared)
            key = coordinator.store.assign(bound.request, bound.revision)
            coordinator.store.transition(key, "preparing")
            paths = materialize(prepared.snapshot, root / "materialized")
            config = {
                "project_root": str(root),
                "snapshot_root": str(paths.root),
                "snapshot_roots": list(map(str, paths.python_path)),
                "external_roots": [str(coordinator.runtime.root), *coordinator.distribution_roots],
                "distribution_roots": coordinator.distribution_roots,
                "reference": bound.request.model,
                "metadata_artifacts": True,
                **_plain(prepared.documents),
            }
            worker = LocalTransport.start(coordinator.runtime.root, external_roots=coordinator.distribution_roots)
            receiver = SegmentReceiver(coordinator.store, key)
            heartbeats, saturated = [], []
            max_bytes = max_files = outstanding = 0
            cancelled = started = None
            ready = None
            acknowledged = False
            try:
                assert worker.receive(10).type == "hello"
                worker.send("run", {"config": config, "request": bound.request.to_dict(), "timeout": 120}, key=key)
                deadline = time.monotonic() + 130
                while time.monotonic() < deadline:
                    message = worker.receive(5)
                    now = time.monotonic()
                    if message.type in ("trace_chunk", "artifact_fragment"):
                        if message.body["segment"]["stream"].startswith("artifact/"):
                            outstanding += len(message.payload)
                            if started is None:
                                started = now
                        else:
                            receipt = receiver.receive(
                                _plain(message.body["segment"]), message.body["fragment_offset"], message.payload
                            )
                            worker.send("ack", asdict(receipt), key=key)
                    elif message.type == "ready":
                        ready = message.body
                    elif message.type == "heartbeat":
                        heartbeats.append(now)
                        body = message.body
                        max_bytes = max(max_bytes, body["spool_bytes"])
                        max_files = max(max_files, body["spool_files"])
                        if body["backpressure"]:
                            saturated.append(now)
                            if args.action == "cancel" and len(saturated) == 3:
                                cancelled = now
                                worker.send("cancel", {}, key=key)
                    elif message.type in ("cancelled", "error", "result"):
                        terminal = message
                        break
                    if ready is not None and not acknowledged:
                        try:
                            receiver.segment_path(ready["resolved"]["id"])
                        except KeyError:
                            continue
                        prefix = receiver.prefix()
                        if prefix is not None and prefix.length >= ready["trace_length"]:
                            acknowledged = True
                            coordinator.store.transition(key, "ready")
                            coordinator.store.transition(key, "running")
                            worker.send("ready_ack", {}, key=key)
                else:
                    raise TimeoutError("No terminal after withheld ACK")
                elapsed = time.monotonic()
                assert saturated, "Workload did not saturate spool"
                assert started is not None, "No artifact credit was withheld"
                assert max_bytes <= 256 << 20 and max_files <= 1024 and outstanding <= 16 << 20
                assert max(b - a for a, b in zip(heartbeats, heartbeats[1:])) < 5
                if args.action == "cancel":
                    assert terminal.type == "cancelled" and cancelled is not None and elapsed - cancelled <= 5
                else:
                    assert terminal.type == "error" and terminal.body["code"] == "disk_full", terminal.body
                pid = int(pid_path.read_text())
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    gone = True
                else:
                    gone = False
                assert gone, "Execution process survived terminal acknowledgement"
                scale = 1 if sys.platform == "darwin" else 1024
                report = {
                    "python": platform.python_version(),
                    "implementation": platform.python_implementation(),
                    "platform": platform.platform(),
                    "action": args.action,
                    "pid": pid,
                    "pid_gone": gone,
                    "spool_peak_observed_bytes": max_bytes,
                    "spool_peak_observed_files": max_files,
                    "withheld_payload_bytes": outstanding,
                    "saturated_heartbeats": len(saturated),
                    "max_heartbeat_gap_seconds": max(b - a for a, b in zip(heartbeats, heartbeats[1:])),
                    "cancel_seconds": None if cancelled is None else elapsed - cancelled,
                    "withheld_ack_to_terminal_seconds": elapsed - started,
                    "terminal": terminal.type,
                    "details": _plain(terminal.body),
                    "parent_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale,
                }
            finally:
                worker.close()
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
