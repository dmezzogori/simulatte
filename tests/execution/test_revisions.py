"""Revision changes revoke physical work atomically and preserve presentation lineage."""

import pytest

from simulatte import Environment, Layout
from simulatte.server import Server
from simulatte.execution import schema
from simulatte.execution.store import RunStore, RevisionConflict, StaleAttempt
from tests.execution.test_schema import request


def layout_bundle():
    with Environment() as env:
        layout = Layout(env)
        Server(env=env, capacity=1, name="machine")
        return layout.prepare().bundle


def active(store):
    revision = store.create_revision({"source": "a"})
    key = store.assign(request(schema), revision)
    store.transition(key, "preparing")
    store.transition(key, "ready")
    store.transition(key, "running")
    return revision, key


def test_presentation_revision_follows_original_attempt_without_revocation(tmp_path):
    with RunStore.open(tmp_path / ".simulatte") as store:
        revision, key = active(store)
        bundle = layout_bundle()
        changed = store.create_revision({"layout": "paint"}, expected=revision, view=(key, bundle))
        assert changed.number == revision.number + 1
        store.require_active(key)
        view = store.presentation_view()
        assert view.revision == changed
        assert view.key == key and view.bundle.full_hash == bundle.full_hash
        assert not view.committed
        assert store.request(key.request_id).layout_revision == 1
        store.revoke(key, "cancelled")
        assert store.presentation_view() is None


def test_physical_revision_revokes_old_assignment_in_same_transaction(tmp_path):
    with RunStore.open(tmp_path / ".simulatte") as store:
        revision, key = active(store)
        next_revision = store.create_revision({"source": "changed"}, expected=revision, revoke_active=True)
        with pytest.raises(StaleAttempt):
            store.require_active(key)
        assert store.attempt(key)["status"] == "superseded"
        replacement = store.assign(request(schema), next_revision)
        store.require_active(replacement)
        with pytest.raises(RevisionConflict):
            store.create_revision({}, expected=revision, revoke_active=True)
        store.require_active(replacement)


def test_layout_aba_does_not_restore_an_old_edit_token(tmp_path):
    with RunStore.open(tmp_path / ".simulatte") as store:
        first = store.create_revision({"layout": "a"})
        second = store.create_revision({"layout": "b"}, expected=first)
        third = store.create_revision({"layout": "a"}, expected=second)
        assert first.digest == third.digest and first.number != third.number
        with pytest.raises(RevisionConflict):
            store.create_revision({"layout": "c"}, expected=first)
        assert store.current_revision() == third


def test_view_binding_requires_readiness_and_current_generation(tmp_path):
    with RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({})
        key = store.assign(request(schema), revision)
        bundle = layout_bundle()
        with pytest.raises(ValueError, match="ready"):
            store.bind_view(revision, key, bundle)
        store.transition(key, "preparing")
        store.transition(key, "ready")
        store.bind_view(revision, key, bundle)
        assert store.presentation_view().key == key
        newer = store.assign(request(schema), revision)
        with pytest.raises(StaleAttempt):
            store.bind_view(revision, key, bundle)
        store.require_active(newer)


def test_ready_view_cannot_attach_to_newer_unprepared_inputs(tmp_path):
    from dataclasses import replace

    with RunStore.open(tmp_path / ".simulatte") as store:
        revision, key = active(store)
        bundle = layout_bundle()
        store.bind_ready_view(key, bundle)
        assert store.presentation_view(revision=revision).key == key
        with pytest.raises(StaleAttempt, match="does not exist"):
            store.bind_view(revision, replace(key, request_id="f" * 64), bundle)
        newer = store.create_revision({"layout": "unprepared"}, expected=revision)
        with pytest.raises(RevisionConflict, match="before view binding"):
            store.bind_view(revision, key, bundle)
        with pytest.raises(StaleAttempt, match="obsolete inputs"):
            store.bind_ready_view(key, bundle)
        assert store.presentation_view(revision=newer) is None


def test_invalid_view_association_rolls_back_revision(tmp_path):
    with RunStore.open(tmp_path / ".simulatte") as store:
        revision, key = active(store)
        store.revoke(key, "cancelled")
        with pytest.raises(StaleAttempt):
            store.create_revision({"layout": "new"}, expected=revision, view=(key, layout_bundle()))
        assert store.current_revision() == revision
        with pytest.raises(ValueError, match="Cannot revoke"):
            store.create_revision({}, expected=revision, revoke_active=True, view=(key, layout_bundle()))
        assert store.current_revision() == revision


def test_completed_inspection_cannot_be_used_as_a_committed_view(tmp_path):
    from simulatte.execution.receipts import SegmentReceiver
    from tests.execution.test_inspection import inspection
    from tests.execution.test_receipts import descriptor

    with RunStore.open(tmp_path / ".simulatte") as store:
        revision = store.create_revision({})
        key = store.assign_inspection(inspection(), revision)
        store.transition(key, "preparing")
        metadata = descriptor(b"metadata", stream="metadata/model-info")
        SegmentReceiver(store, key).receive(metadata, 0, b"metadata")
        store.complete_inspection(key, metadata, execution_exited=True, descendants_cleaned=True)
        with pytest.raises(ValueError, match="indexed simulation result"):
            store.bind_view(revision, key, layout_bundle())
        assert store.presentation_view() is None
