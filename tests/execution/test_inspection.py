"""Inspection has a real fenced identity without a fabricated simulation request."""

import pytest

from simulatte.execution import schema
from simulatte.execution.receipts import SegmentReceiver
from simulatte.execution.store import RunStore, StaleAttempt
from tests.execution.test_receipts import descriptor


def inspection(**changes):
    assert hasattr(schema, "InspectionRequest"), "typed inspection request missing"
    return schema.InspectionRequest(
        model="plant.py", source_hash="a" * 64, runtime=changes.pop("runtime", {}), **changes
    )


def test_inspection_identity_roundtrips_and_is_domain_separated():
    request = inspection(runtime={"python": "3.11"})
    assert schema.InspectionRequest.from_dict(request.to_dict()) == request
    assert schema.inspection_id(request) == schema.inspection_id(inspection(runtime={"python": "3.11"}))
    assert schema.inspection_id(request) != schema.inspection_id(inspection(runtime={"python": "3.12"}))
    assert "seed" not in request.to_dict() and "stopping" not in request.to_dict()
    with pytest.raises(ValueError):
        schema.ExecutionRequest.from_dict(request.to_dict())


@pytest.mark.parametrize(
    "model",
    [None, "", "../plant.py", "/plant.py", "plant.py:", "plant.py:bad-name", "plant.py:build:other", "plant.txt"],
)
def test_inspection_rejects_nonlocal_or_ambiguous_reference(model):
    assert hasattr(schema, "InspectionRequest")
    with pytest.raises(ValueError):
        schema.InspectionRequest(model=model, source_hash="a" * 64, runtime={})


def test_inspection_completion_needs_durable_metadata_and_cleanup(tmp_path):
    request = inspection()
    with RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({"source": request.source_hash})
        key = store.assign_inspection(request, revision)
        assert key.request_id == schema.inspection_id(request)
        assert store.inspection_request(key.request_id) == request
        store.transition(key, "preparing")
        metadata = descriptor(b"metadata", stream="metadata/model-info")
        with pytest.raises(ValueError, match="receipt"):
            store.complete_inspection(key, metadata, execution_exited=True, descendants_cleaned=True)
        receiver = SegmentReceiver(store, key)
        receiver.receive(metadata, 0, b"metadata")
        for exited, cleaned in ((False, True), (True, False)):
            with pytest.raises(ValueError, match="cleanup"):
                store.complete_inspection(key, metadata, execution_exited=exited, descendants_cleaned=cleaned)
        store.complete_inspection(key, metadata, execution_exited=True, descendants_cleaned=True)
        assert store.attempt(key)["status"] == "succeeded"
        with pytest.raises(KeyError):
            store.result(key.attempt_id)
        with pytest.raises(ValueError):
            store.request(key.request_id)
        with pytest.raises(StaleAttempt):
            store.complete_inspection(key, metadata, execution_exited=True, descendants_cleaned=True)
    with RunStore.open(tmp_path / ".simulatte") as reopened:
        assert reopened.inspection_request(key.request_id) == request
        assert reopened.attempt(key)["status"] == "succeeded"


def test_superseded_inspection_cannot_publish_metadata(tmp_path):
    with RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({"source": "a"})
        old = store.assign_inspection(inspection(), revision)
        store.assign_inspection(inspection(), revision)
        with pytest.raises(StaleAttempt):
            store.complete_inspection(
                old, descriptor(b"x", stream="metadata/model-info"), execution_exited=True, descendants_cleaned=True
            )


def test_inspection_lookup_detects_missing_and_tampered_identity(tmp_path):
    from simulatte.execution.store import _blob

    with RunStore.open(tmp_path / "store") as store:
        with pytest.raises(KeyError):
            store.inspection_request("a" * 64)
        request = inspection()
        key = store.assign_inspection(request, store.create_revision({}))
        changed = request.to_dict() | {"runtime": {"changed": True}}
        with store._transaction() as db:
            db.execute("UPDATE requests SET body=? WHERE id=?", (_blob(changed), key.request_id))
        with pytest.raises(ValueError, match="identity"):
            store.inspection_request(key.request_id)


def test_inspection_cannot_complete_wrong_stream_state_or_pending_upload(tmp_path):
    from simulatte.execution.protocol import MAX_FRAGMENT

    with RunStore.open(tmp_path / "store") as store:
        key = store.assign_inspection(inspection(), store.create_revision({}))
        reference = descriptor(b"metadata", stream="metadata/model-info")
        with pytest.raises(ValueError, match="reference"):
            store.complete_inspection(
                key, reference | {"stream": "trace"}, execution_exited=True, descendants_cleaned=True
            )
        with pytest.raises(ValueError, match="preparing"):
            store.complete_inspection(key, reference, execution_exited=True, descendants_cleaned=True)
        store.transition(key, "preparing")
        receiver = SegmentReceiver(store, key)
        receiver.receive(reference, 0, b"metadata")
        data = b"x" * (MAX_FRAGMENT + 1)
        pending = descriptor(data, id="second", sequence=2, stream="metadata/resolved-manifest")
        receiver.receive(pending, 0, data[:MAX_FRAGMENT])
        with pytest.raises(ValueError, match="incomplete"):
            store.complete_inspection(key, reference, execution_exited=True, descendants_cleaned=True)
        assert store.attempt(key)["status"] == "preparing"


@pytest.mark.parametrize("change", [{"schema": 2}, {"kind": "execution"}, {"extra": True}])
def test_inspection_schema_is_versioned_and_closed(change):
    with pytest.raises(ValueError):
        schema.InspectionRequest.from_dict(inspection().to_dict() | change)
