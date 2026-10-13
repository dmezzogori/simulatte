"""Headless `simulatte replay`: one JSON envelope; exit 0 verified, 1 unverified or failed, 2 usage."""

import sys
from unittest.mock import patch

from simulatte.cli import main
from simulatte.execution import coordinator as m
from simulatte.execution.store import RunStore
from tests.execution.test_coordinator import project
from tests.execution.test_experiment_cli import NO_WEB, envelope
from tests.execution.test_replay import tamper
from tests.execution.test_scheduler import Peer, write_experiment


def test_replay_prints_verification_and_exits_by_outcome(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SIMULATTE_WORKERS", str(tmp_path / "workers.toml"))  # No configured workers.
    spec = project(tmp_path)
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    root = str(spec.root)
    with patch.dict(sys.modules, NO_WEB):
        assert main(["experiment", "run", str(path), "--project", root, "--local-slots", "1", "--json"]) == 0
        run_id = envelope(capsys)["run_id"]
        with RunStore.open(spec.root / ".simulatte") as store:
            (replication,) = store.replications(run_id)
        identity = replication["request_id"]
        assert main(["replay", identity, "--project", root, "--timeout", "20", "--json"]) == 0
        verified = envelope(capsys)
        assert set(verified) == {"status", "request_id", "original", "attempt_id", "verified", "reason", "path"}
        assert (verified["status"], verified["original"], verified["verified"], verified["reason"]) == (
            "succeeded",
            identity,
            True,
            None,
        )
        assert verified["request_id"] != identity
        assert main(["replay", identity, "--project", root]) == 0
        line = capsys.readouterr().err
        assert (
            line.startswith(f"verified: replay {verified['request_id']} of {identity} attempt ")
            and line.count("\n") == 1
        )
        with RunStore.open(spec.root / ".simulatte") as store:
            assert store.replay(verified["request_id"])["attempt"] != verified["attempt_id"]
            tamper(store, replication["attempt"], "fingerprint", lambda value: value.update(digest="0" * 64))
        assert main(["replay", identity, "--project", root, "--json"]) == 1
        unverified = envelope(capsys)
        assert (unverified["status"], unverified["verified"], unverified["reason"]) == (
            "succeeded",
            False,
            "digest_mismatch",
        )
        assert unverified["path"].endswith(unverified["attempt_id"])
        assert main(["replay", identity, "--project", root]) == 1
        line = capsys.readouterr().err
        assert line.startswith("unverified (digest_mismatch): replay ") and line.count("\n") == 1
        assert main(["replay", identity, "--project", root, "--worker", "strangelove", "--json"]) == 1
        assert envelope(capsys)["status"] == "no_compatible_worker"
        assert main(["replay", identity, "--project", root, "--worker", "strangelove"]) == 1
        assert capsys.readouterr().err.startswith("Worker strangelove is not configured: Unknown worker")
        # A failed replay names its attempt; an interrupt cancels; other failures exit 1.
        with patch.object(m.LocalTransport, "start", lambda *args, **kwargs: Peer()):
            assert main(["replay", identity, "--project", root, "--json"]) == 1
        lost = envelope(capsys)
        assert lost["status"] == "lost" and lost["request_id"] == verified["request_id"] and lost["attempt_id"]
        for error, code, status in ((KeyboardInterrupt(), 130, "cancelled"), (RuntimeError("busy"), 1, "failed")):
            with patch.object(m.Coordinator, "replay", side_effect=error):
                assert main(["replay", identity, "--project", root, "--json"]) == code
            assert envelope(capsys)["status"] == status
        assert main(["replay", "0" * 64, "--project", root, "--json"]) == 2
        assert envelope(capsys)["status"] == "validation_failed"
        assert main(["replay", identity, "--project", str(tmp_path), "--json"]) == 2
        assert envelope(capsys)["status"] == "validation_failed"
