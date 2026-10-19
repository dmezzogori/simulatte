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
