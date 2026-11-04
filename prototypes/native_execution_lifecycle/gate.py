"""Installed cancellation/failure gate; wait for actual cleanup, not store revocation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.schema import _plain
from simulatte.execution.snapshot import discover_project
import simulatte_native

KINDS = (
    "cancel_boundary",
    "cancel_advance_short",
    "cancel_advance",
    "cancel_advance_ignore_term",
    "timeout_advance",
    "cancel_admission",
    "late_cancel",
    "producer_error",
    "callback_error",
    "invalid_input",
    "advance_error",
    "cleanup_error",
    "close_error",
    "process_exit",
)
EXPECTED = {
    "producer_error": "primary producer failure",
    "callback_error": "primary input callback failure",
    "advance_error": "primary post-advance failure",
    "cleanup_error": "primary producer failure",
    "close_error": "primary close failure",
    "history_error": "primary history commit failure",
    "allocation_error": "allocation",
}


def table():
    data = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,rss="], text=True)
    return {int(pid): (int(ppid), int(rss) * 1024) for pid, ppid, rss in (line.split() for line in data.splitlines())}


def wait_for(function, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(0.01)
    raise TimeoutError("lifecycle gate condition not reached")


def marks(directory):
    return {p.stem: json.loads(p.read_text()) for p in directory.glob("*.json")}


def parent_rss():
    return table()[os.getpid()][1]


def main(args):
    assert shutil.which("cc") is None and shutil.which("clang") is None
    project = args.output.parent / "project"
    project.mkdir()
    shutil.copyfile(Path(__file__).with_name("plant.py"), project / "plant.py")
    (project / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    rows = []

    def save():
        args.output.write_text(
            json.dumps(
                dict(
                    rows=rows,
                    python=sys.version,
                    platform=platform.platform(),
                    provider=simulatte_native.info(),
                    parent_pid=os.getpid(),
                    instrumented=args.instrumented,
                ),
                indent=2,
            )
            + "\n"
        )

    callbacks = []
    spec = discover_project(project / "plant.py", project)
    with Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=30)
        baselines = {}
        entry = {}

        def execute(retention, kind, number):
            directory = args.output.parent / f"{retention}-{number:03d}-{kind}"
            directory.mkdir()
            parameters = dict(retention=retention, kind=kind, diagnostic=str(directory), jobs=args.jobs)
            request = coordinator.make_request(prepared, parameters=parameters, level="kpi")
            budget = max(3, entry[retention] + 1.5) if kind == "timeout_advance" else 30
            started = time.monotonic()
            key = coordinator.submit(request, prepared.revision, timeout=budget)
            cancelled = None
            marker = None
            if kind.startswith("cancel") or kind == "timeout_advance" or kind == "late_cancel":
                marker = (
                    "busy"
                    if "advance" in kind
                    else "boundary"
                    if kind == "cancel_boundary"
                    else "admitting"
                    if kind == "cancel_admission"
                    else "late"
                )

                def ready():
                    if (directory / (marker + ".json")).exists():
                        return True
                    status = coordinator.store.attempt(key)["status"]
                    assert status not in ("failed", "cancelled", "succeeded", "validation_failed"), (
                        coordinator.store.attempt(key)
                    )
                    return False

                wait_for(ready)
                before = marks(directory)
                assert before[marker]["progressed"]["storage"]["completed"] == 1
                if marker == "busy":
                    assert before[marker]["binding_close_rejected"] and "native-returned" not in before
                    if retention not in entry:
                        entry[retention] = time.monotonic() - started
                if marker == "late":
                    assert before["closed"]["engine_released"]
                if kind != "timeout_advance":
                    cancelled = time.monotonic()
                    assert coordinator.cancel(key)
                    if kind == "late_cancel":
                        (directory / "release").write_text("continue\n")
            expected = (
                "succeeded"
                if kind == "healthy"
                else "cancelled"
                if kind.startswith("cancel") or kind == "late_cancel"
                else "failed"
            )
            try:
                result = coordinator.wait(key, timeout=40)
            except ExecutionFailure:
                assert expected != "succeeded", coordinator.store.attempt(key)
                result = None
            else:
                assert expected == "succeeded"
            wait_for(lambda: coordinator._actors["interactive"].current is None, timeout=10)
            observed = marks(directory)
            assert "progress" in observed
            pids = {value["pid"] for value in observed.values()}
            pids.update(value["child_pid"] for name, value in observed.items() if name == "descendant")
            wait_for(lambda: not (pids & table().keys()), timeout=5)
            done = time.monotonic()
            attempt = coordinator.store.attempt(key)
            assert attempt["status"] == expected, attempt
            if cancelled is not None:
                assert done - cancelled < 5, ("slow cancellation", done - cancelled)
            if kind == "timeout_advance":
                assert done - started < budget + 5
                assert attempt["details"]["code"] == "timeout", attempt
            if kind in EXPECTED:
                assert EXPECTED[kind] in attempt["details"]["message"], attempt
            if kind == "cleanup_error":
                assert "secondary cleanup failure" in attempt["details"]["traceback"], attempt
            if kind in ("cancel_advance", "cancel_advance_ignore_term"):
                assert "native-returned" not in observed, "native call completed instead of being stopped"
                assert "closed" not in observed, "forced termination unexpectedly ran Python close"
            if kind == "cancel_advance_ignore_term":
                assert done - cancelled >= 2.5, "SIGKILL escalation not exercised"
                assert "descendant" in observed
            if expected != "succeeded":
                with coordinator.store._transaction() as db:
                    count = db.execute("SELECT COUNT(*) FROM results WHERE attempt=?", (key.attempt_id,)).fetchone()[0]
                assert count == 0
                try:
                    coordinator.store.result(key.attempt_id)
                except KeyError:
                    pass
                else:
                    raise AssertionError("aborted attempt published a successful result")
            else:
                fingerprint = _plain(result.fingerprint)
                artifact = (result.path / "artifacts/final.json").read_bytes()
                assert baselines.setdefault(retention, (fingerprint, artifact)) == (fingerprint, artifact)
                assert observed["closed"]["engine_released"]
                assert result.key.attempt_id == key.attempt_id
            row = dict(
                retention=retention,
                kind=kind,
                attempt=key.attempt_id,
                request=key.request_id,
                status=expected,
                details=_plain(attempt["details"]),
                markers=observed,
                elapsed_seconds=done - started,
                cancel_to_cleanup_seconds=done - cancelled if cancelled is not None else None,
                deadline_seconds=budget,
                child_pids=sorted(pids),
                parent_rss=parent_rss(),
                parent_handles=len(os.listdir("/proc/self/fd" if Path("/proc/self/fd").exists() else "/dev/fd")),
                fingerprint=_plain(result.fingerprint) if result else None,
                artifact_sha256=hashlib.sha256((result.path / "artifacts/final.json").read_bytes()).hexdigest()
                if result
                else None,
            )
            rows.append(row)
            callbacks.extend(coordinator.events())
            save()
            print(
                json.dumps(dict(retention=retention, kind=kind, status=expected, seconds=row["elapsed_seconds"])),
                flush=True,
            )
            return row

        selected = {}
        for retention in ("full", "bounded"):
            kinds = (
                ("allocation_error",)
                if args.instrumented
                else (
                    (
                        "cancel_boundary",
                        "cancel_advance",
                        "producer_error",
                        "advance_error",
                        "cleanup_error",
                        "close_error",
                    )
                    if args.quick
                    else KINDS
                )
            )
            if retention == "bounded" and not args.instrumented:
                kinds = (*kinds, "history_error")
            if not args.quick and not args.instrumented:
                kinds = (*kinds, "cancel_advance", "producer_error", "cancel_advance", "producer_error")
            selected[retention] = execute(retention, "healthy", len(rows))
            for kind in kinds:
                execute(retention, kind, len(rows))
                execute(retention, "healthy", len(rows))
        (project / "plant.py").unlink()
        for retention, row in selected.items():
            result = coordinator.reproduce(row["request"], timeout=30)
            assert _plain(result.fingerprint) == row["fingerprint"]
            assert (
                hashlib.sha256((result.path / "artifacts/final.json").read_bytes()).hexdigest()
                == row["artifact_sha256"]
            )
            rows.append(
                dict(
                    kind="source_deleted_reproduction",
                    retention=retention,
                    attempt=result.key.attempt_id,
                    request=result.key.request_id,
                    fingerprint=_plain(result.fingerprint),
                    status="succeeded",
                )
            )
        assert len({row["attempt"] for row in rows}) == len(rows)
        with coordinator.store._transaction() as db:
            published = db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        assert published == sum(row["status"] == "succeeded" for row in rows)
        if not args.instrumented:
            assert (
                max(row["parent_rss"] for row in rows if "parent_rss" in row)
                - min(row["parent_rss"] for row in rows if "parent_rss" in row)
                < 16 * 2**20
            )
            assert (
                max(row["parent_handles"] for row in rows if "parent_handles" in row)
                - min(row["parent_handles"] for row in rows if "parent_handles" in row)
                <= 4
            )
        save()
        (args.output.parent / "events.json").write_text(json.dumps(callbacks, indent=2) + "\n")
    print(json.dumps(dict(passed=True, attempts=len(rows), published=published)), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--instrumented", action="store_true")
    p.add_argument("--jobs", type=int, default=160000)
    main(p.parse_args())
