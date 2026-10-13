"""Full-level replays linked `replay-of` and verified against the original (design 6, D100)."""

from dataclasses import replace
from pathlib import Path

import pytest

from simulatte import Trace
from simulatte.execution import coordinator as m
from simulatte.execution.publication import PublishedResult
from simulatte.execution.replay import Verification, verify_replay
from simulatte.execution.schema import AttemptKey, request_id
from simulatte.execution.slots import SlotConfig
from simulatte.execution.store import _blob, _read
from simulatte.experiments import load_experiment
from tests.execution.test_coordinator import project
from tests.execution.test_scheduler import Peer, write_experiment


def tamper(store, attempt, column, change):
    """Rewrite a stored ``results`` blob (``fingerprint`` or ``manifest``) of one attempt in place.

    Simulates an original whose recorded outcome differs from what its replay reproduces.
    """
    with store._transaction() as db:
        value = _read(db.execute(f"SELECT {column} FROM results WHERE attempt=?", (attempt,)).fetchone()[0])
        change(value)
        db.execute(f"UPDATE results SET {column}=? WHERE attempt=?", (_blob(value), attempt))


def kpi_original(coordinator, **parameters):
    prepared = coordinator.prepare(timeout=10)
    request = coordinator.make_request(prepared, parameters=parameters, level="kpi")
    key = coordinator.submit(request, prepared.revision, timeout=20)
    return coordinator.wait(key, timeout=25)


def test_kpi_replication_replayed_at_full_is_verified(tmp_path):
    spec = project(tmp_path)
    path = write_experiment(spec.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path))
        assert coordinator.wait_experiment(run_id, timeout=30)["status"] == "completed"
        (replication,) = coordinator.store.replications(run_id)
        identity = replication["request_id"]
        original = coordinator.store.result(replication["attempt"])
        assert original.manifest["request"]["level"] == "kpi"
        coordinator.events()
        outcome = coordinator.replay(identity, timeout=20)
        assert outcome.verification == Verification(True, None)
        replayed = coordinator.store.result(outcome.key.attempt_id)
        assert replayed.manifest["request"]["level"] == "full"
        assert Trace.open(replayed.path / "run.simtrace").verify() is True
        assert replayed.fingerprint["digest"] == original.fingerprint["digest"]
        assert coordinator.store.attempt(outcome.key)["slot"] == "local-1"
        expected = replace(coordinator.store.request(identity), level="full")
        assert outcome.key.request_id == request_id(expected) != identity
        assert coordinator.store.replay(outcome.key.request_id) == {
            "request_id": outcome.key.request_id,
            "original": identity,
            "attempt": outcome.key.attempt_id,
            "status": "succeeded",
            "verified": True,
            "details": {"reason": None},
        }
        states = [event["body"] for event in coordinator.events() if event["type"] == "replay_state"]
        assert [(state["status"], state["verified"]) for state in states] == [("running", None), ("succeeded", True)]
        assert {state["original"] for state in states} == {identity}
        # The original result, its request and its replication are untouched.
        assert coordinator.store.result(replication["attempt"]) == original
        assert coordinator.store.request(identity).level == "kpi"
        assert coordinator.store.replications(run_id) == [replication]
        # Replaying the replay request replays the original again, under the same link.
        again = coordinator.replay(outcome.key.request_id, timeout=20)
        assert again.verification.verified and again.key.request_id == outcome.key.request_id
        assert again.key.attempt_id != outcome.key.attempt_id
        assert coordinator.store.replay(outcome.key.request_id)["original"] == identity
        # A full original replays as a plain re-execution linked to itself.
        prepared = coordinator.prepare(timeout=10)
        full = coordinator.make_request(prepared, parameters={"n": 4})
        committed = coordinator.wait(coordinator.submit(full, prepared.revision, timeout=20), timeout=25)
        itself = coordinator.replay(request_id(full), timeout=20)
        assert itself.verification.verified and itself.key.request_id == request_id(full)
        assert coordinator.store.replay(request_id(full))["original"] == request_id(full)
        assert coordinator.store.result(committed.key.attempt_id) == committed


def test_digest_mismatch_is_unverified_not_hidden(tmp_path):
    spec = project(tmp_path)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        original = kpi_original(coordinator, n=3)
        tamper(coordinator.store, original.key.attempt_id, "fingerprint", lambda value: value.update(digest="0" * 64))
        outcome = coordinator.replay(original.key.request_id, timeout=20)
        assert outcome.verification == Verification(False, "digest_mismatch")
        # Unverified replays are published and labelled, never hidden.
        replayed = coordinator.store.result(outcome.key.attempt_id)
        assert (replayed.path / "run.simtrace").is_file()
        record = coordinator.store.replay(outcome.key.request_id)
        assert (record["status"], record["verified"], record["details"]) == (
            "succeeded",
            False,
            {"reason": "digest_mismatch"},
        )
        # Runtime facts differ from the original's: the next replay names that field.
        tamper(
            coordinator.store,
            original.key.attempt_id,
            "fingerprint",
            lambda value: value.update(digest=replayed.fingerprint["digest"]),
        )
        tamper(
            coordinator.store,
            original.key.attempt_id,
            "manifest",
            lambda value: value["resolved"]["runtime"]["facts"].update(python="2.7.18"),
        )
        assert coordinator.replay(original.key.request_id, timeout=20).verification == Verification(
            False, "runtime_mismatch"
        )
        assert coordinator.store.replay(outcome.key.request_id)["details"] == {"reason": "runtime_mismatch"}


def published(digest="a" * 64, kpis=None, facts=None, dependencies="d" * 64, packages=None, physical="p" * 64):
    """A stand-in published result carrying only the fields verification reads."""
    resolved = {
        "runtime": {"facts": facts or {"python": "3.14.1"}, "dependencies_hash": dependencies},
        "dependencies": {"packages": packages or {"simpy": {"version": "4.1.1"}}},
        "layout": {"physical_hash": physical, "full_hash": "f" * 64},
    }
    return PublishedResult(
        AttemptKey("1" * 64, "2" * 32, 1),
        Path("unused"),
        {"resolved": resolved},
        {"digest": digest, "kpis": kpis or {}},
    )


def test_verification_reports_the_first_differing_field():
    base = published(kpis={"shop/throughput": 1.0, "shop/wip": 2.5})
    assert verify_replay(base, base, True) == Verification(True, None)
    for value in (False, "not_verifiable"):
        assert verify_replay(base, published(digest="b" * 64), value) == Verification(False, "trace_unverified")
    cases = [
        (published(digest="b" * 64, kpis={"shop/wip": 9.0}), "digest_mismatch"),
        # Only KPIs present in both fingerprints are compared; the first differing key is named.
        (published(kpis={"shop/throughput": 1.0, "shop/wip": 3.0, "only/replay": 1.0}), "kpi_mismatch:shop/wip"),
        (published(kpis={"shop/wip": 2.5}, facts={"python": "3.13.0"}), "runtime_mismatch"),
        (published(kpis={"shop/wip": 2.5}, dependencies="e" * 64), "dependencies_mismatch"),
        (published(kpis={"shop/wip": 2.5}, packages={"simpy": {"version": "4.0.0"}}), "dependencies_mismatch"),
        (published(kpis={"shop/wip": 2.5}, physical="q" * 64), "layout_mismatch"),
    ]
    for replayed, reason in cases:
        assert verify_replay(base, replayed, True) == Verification(False, reason)
    assert verify_replay(published(kpis={"a": float("nan")}), published(kpis={"a": float("nan")}), True).verified


def test_no_compatible_worker_and_uncommitted_requests_fail_before_scheduling(tmp_path, monkeypatch):
    spec = project(tmp_path)
    with m.Coordinator(spec, slots=SlotConfig(local=1)) as coordinator:
        original = kpi_original(coordinator, n=3)
        identity = original.key.request_id
        # A failed replay is recorded with its attempt status, unverified, and raised.
        with monkeypatch.context() as patch:
            patch.setattr(m.LocalTransport, "start", lambda *args, **kwargs: Peer())
            with pytest.raises(m.ExecutionFailure) as lost:
                coordinator.replay(identity, timeout=20)
        assert lost.value.status == "lost"
        record = coordinator.store.replay(lost.value.key.request_id)
        assert (record["status"], record["verified"], record["attempt"]) == ("lost", None, lost.value.key.attempt_id)
        before = coordinator.store.slots()
        with pytest.raises(m.ExecutionFailure) as failure:
            coordinator.replay(identity, worker="strangelove")
        assert failure.value.status == "no_compatible_worker" and "strangelove" in str(failure.value)
        # The original attempt ran on a worker this pool does not have.
        with coordinator.store._transaction() as db:
            db.execute("UPDATE attempts SET worker='strangelove' WHERE id=?", (original.key.attempt_id,))
        with pytest.raises(m.ExecutionFailure, match="strangelove"):
            coordinator.replay(identity)
        assert coordinator.replay(identity, worker="local", timeout=20).verification.verified
        with coordinator.store._transaction() as db:
            db.execute("UPDATE attempts SET worker=NULL WHERE id=?", (original.key.attempt_id,))
        assert {name: slot["active"] for name, slot in coordinator.store.slots().items()} == {
            name: None for name in {*before, "local-1"}
        }
        prepared = coordinator.prepare(timeout=10)
        pending = coordinator.make_request(prepared, parameters={"n": 4}, level="kpi")
        coordinator.store.assign(pending, prepared.revision)
        with pytest.raises(ValueError, match="No committed result to replay"):
            coordinator.replay(request_id(pending))
        with pytest.raises(KeyError):
            coordinator.replay("0" * 64)
        with pytest.raises(ValueError, match="Timeout"):
            coordinator.replay(identity, timeout=0)
        # Every compatible slot busy, or a refusal other than busy, schedules nothing.
        for message, expected in (("Slot local-1 is busy", "Every compatible slot is busy"), ("closed", "closed")):

            def refuse(*args, message=message, **kwargs):
                raise RuntimeError(message)

            with monkeypatch.context() as patch:
                patch.setattr(coordinator, "_schedule", refuse)
                with pytest.raises(RuntimeError, match=expected):
                    coordinator.replay(identity)
        with coordinator.store._transaction() as db:
            db.execute("DELETE FROM snapshots WHERE id=? AND kind='preparation'", (identity,))
        with pytest.raises(ValueError, match="preparation is unavailable"):
            coordinator.replay(identity)


def test_replay_without_experiment_slots_uses_the_interactive_slot_and_cancels_on_interrupt(tmp_path, monkeypatch):
    spec = project(tmp_path)
    with m.Coordinator(spec, slots=SlotConfig(local=0)) as coordinator:
        original = kpi_original(coordinator, n=3)
        outcome = coordinator.replay(original.key.request_id, timeout=20)
        assert outcome.verification.verified
        assert coordinator.store.attempt(outcome.key)["slot"] == "interactive"

        def interrupted(key, *, timeout=None):
            raise KeyboardInterrupt

        monkeypatch.setattr(coordinator, "wait", interrupted)
        with pytest.raises(KeyboardInterrupt):
            coordinator.replay(original.key.request_id, timeout=20)
        record = coordinator.store.replay(outcome.key.request_id)
        assert (record["status"], record["verified"]) == ("cancelled", None)
        assert record["attempt"] != outcome.key.attempt_id
