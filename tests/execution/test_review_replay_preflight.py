"""Unscheduled replay outcomes survive reconnect without overwriting another owner's admission."""

import pytest

from simulatte.execution import schema
from simulatte.execution.store import RunStore
from tests.execution.test_schema import request


def test_preflight_outcome_is_durable_and_fenced_by_admitted_attempt(tmp_path):
    root = tmp_path / ".simulatte"
    with RunStore.open(root) as store:
        key = store.assign(request(schema), store.create_revision({}))
        replay = "e" * 64
        with pytest.raises(KeyError):
            store.fail_replay_preflight(replay, "f" * 64, None, {})
        assert store.fail_replay_preflight(replay, key.request_id, None, {"message": "missing preparation"})
        with pytest.raises(ValueError, match="another original"):
            other = store.assign(request(schema, seed=42), store.current_revision())
            store.fail_replay_preflight(replay, other.request_id, None, {})
        store.put_replay(replay, key.request_id, attempt=key.attempt_id)
        assert not store.fail_replay_preflight(replay, key.request_id, None, {})
        assert not store.fail_replay_preflight(replay, key.request_id, key.attempt_id, {})
        assert store.replay(replay)["status"] == "running"
        store.set_replay(replay, key.attempt_id, "cancelled")
        assert store.fail_replay_preflight(replay, key.request_id, key.attempt_id, {"message": "unavailable"})
    with RunStore.open(root) as store:
        assert store.replay(replay) == {
            "request_id": replay,
            "original": key.request_id,
            "attempt": None,
            "status": "failed",
            "verified": None,
            "details": {"message": "unavailable"},
        }


@pytest.mark.parametrize("retry", [False, True])
def test_latest_unscheduled_replay_survives_bounded_reconnect_history(tmp_path, monkeypatch, retry):
    from simulatte.studio.experiments import replays, MAX_LISTED
    from simulatte.execution import store as module

    clock = [100.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    root = tmp_path / ".simulatte"
    with RunStore.open(root) as store:
        for index in range(MAX_LISTED + 2):
            key = store.assign(request(schema, seed=index), store.create_revision({}))
            store.revoke(key, "cancelled")
            replay = schema.request_id(request(schema, seed=index + 100))
            store.put_replay(replay, key.request_id, attempt=key.attempt_id)
            store.set_replay(replay, key.attempt_id, "cancelled")
            if index == 0:
                oldest, original = replay, key
            clock[0] += 1
        before = store.request(original.request_id).to_dict()
        if not retry:
            oldest = "f" * 64
        assert store.fail_replay_preflight(
            oldest, original.request_id, original.attempt_id if retry else None, {"message": "latest failure"}
        )
    with RunStore.open(root) as store:
        with store._transaction() as db:
            rows = replays(db, lambda db, attempt: None)
        assert len(rows) == MAX_LISTED
        assert rows[0]["request_id"] == oldest
        assert rows[0]["status"] == "failed" and rows[0]["reason"] == "latest failure"
        assert rows[0]["key"] is None
        assert store.request(original.request_id).to_dict() == before
        assert store.attempt(original)["status"] == "cancelled"
