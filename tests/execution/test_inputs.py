"""Coordinator-owned inputs use one revision boundary for validated Apply."""

import pytest

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.store import RevisionConflict
from simulatte.execution.schema import AttemptKey
from tests.execution.test_coordinator import MODEL, project


PRESENTATION_MODEL = MODEL.replace(
    "    cell = Cell(env)",
    "    from simulatte.server import Server\n    Server(env=env, capacity=1, name='machine')\n    cell = Cell(env)",
)


def test_apply_is_typed_atomic_and_does_not_repeat_inspection(tmp_path, monkeypatch):
    with Coordinator(project(tmp_path)) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        owner = coordinator.inputs
        first = owner.bind(prepared)

        def forbidden(*args, **kwargs):
            pytest.fail("parameter Apply must reuse captured preparation")

        monkeypatch.setattr(coordinator, "prepare", forbidden)
        assert owner.apply(first.revision, {"parameters": {"n": 2}}, auto_run=False) == first
        changed = owner.apply(first.revision, {"parameters": {"n": 4}, "seed": 9}, timeout=10)
        queued = [event for event in coordinator.events() if event["type"] == "queued"]
        key = AttemptKey(**queued[-1]["key"])
        assert changed.revision.number == first.revision.number + 1
        assert changed.request.parameters["n"] == 4 and changed.request.seed == 9
        assert changed.request.layout_revision == changed.revision.number
        with pytest.raises(ValueError):
            owner.apply(changed.revision, {"parameters": {"n": "4"}}, auto_run=False)
        assert owner.state == changed
        with pytest.raises(RevisionConflict):
            owner.apply(first.revision, {"seed": 1}, auto_run=False)
        assert owner.state == changed
        result = coordinator.wait(key, timeout=15)
        assert (result.path / "artifacts/answer.txt").read_text() == "9"


def test_required_parameters_can_be_displayed_before_apply(tmp_path):
    from tests.execution.test_coordinator import MODEL

    with Coordinator(project(tmp_path, MODEL.replace("n: int = 2", "n: int"))) as coordinator:
        state = coordinator.inputs.bind(coordinator.prepare(timeout=10))
        assert state.request is None and "n" in state.issues
        changed = coordinator.inputs.apply(state.revision, {"parameters": {"n": 3}}, auto_run=False)
        assert changed.request.parameters["n"] == 3 and not changed.issues


def test_schema_refresh_retains_valid_fields_and_flags_incompatible_values(tmp_path):
    from tests.execution.test_coordinator import MODEL

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        initial = coordinator.inputs.bind(coordinator.prepare(timeout=10))
        applied = coordinator.inputs.apply(
            initial.revision,
            {"parameters": {"n": 7}, "seed": 11, "stopping": {"type": "horizon", "horizon": 7, "warmup": 1}},
            auto_run=False,
        )
        (spec.root / "plant.py").write_text(MODEL.replace("n: int = 2", "n: str = 'default'"))
        prepared = coordinator.prepare(expected=applied.revision, timeout=10)
        refreshed = coordinator.inputs.bind(prepared)
        assert refreshed.options["parameters"]["n"] == "default"
        assert refreshed.options["seed"] == "11"
        assert refreshed.options["stopping"] == applied.options["stopping"]
        assert "n" in refreshed.issues and refreshed.request is None
        accepted = coordinator.inputs.apply(refreshed.revision, {}, auto_run=False)
        assert not accepted.issues and accepted.request.parameters["n"] == "default"


def test_input_revision_and_preparation_commit_roll_back_together(tmp_path):
    from dataclasses import replace
    from simulatte.execution.schema import request_id

    with Coordinator(project(tmp_path)) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        request = coordinator._build_request(prepared)
        wrong = replace(request, layout_revision=prepared.revision.number + 2)
        manifest = coordinator._preparation_manifest(prepared, wrong)
        with pytest.raises(RevisionConflict, match="new revision"):
            coordinator.store.create_revision({}, expected=prepared.revision, preparation=manifest)
        assert coordinator.store.current_revision() == prepared.revision
        with coordinator.store._transaction() as db:
            assert db.execute("SELECT 1 FROM snapshots WHERE id=?", (request_id(wrong),)).fetchone() is None


def test_presentation_validation_reuses_only_exact_current_revision(tmp_path, monkeypatch):
    with Coordinator(project(tmp_path, PRESENTATION_MODEL)) as coordinator:
        state = coordinator.inputs.bind(coordinator.prepare(timeout=10))
        key = coordinator.submit(state.request, state.revision, timeout=10)
        coordinator.wait(key, timeout=15)

        def forbidden(*args, **kwargs):
            pytest.fail("presentation validation must not start a child")

        monkeypatch.setattr(coordinator, "submit", forbidden)
        patch = {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "visual": {"color": "red"}}}}
        validation = coordinator.inputs.validate_layout(state.revision, patch)
        assert not validation.requires_rerun
        assert validation.key == key and validation.bundle is not None
        changed = coordinator.inputs.apply(state.revision, {"seed": 8}, auto_run=False)
        validation = coordinator.inputs.validate_layout(changed.revision, patch)
        assert validation.requires_rerun and validation.bundle is None
        with pytest.raises(RevisionConflict):
            coordinator.inputs.validate_layout(state.revision, patch)


def test_presentation_write_preserves_result_and_does_not_launch_child(tmp_path, monkeypatch):
    import json

    with Coordinator(project(tmp_path, PRESENTATION_MODEL)) as coordinator:
        owner = coordinator.inputs
        initial = owner.bind(coordinator.prepare(timeout=10))
        key = coordinator.submit(initial.request, initial.revision, timeout=10)
        result = coordinator.wait(key, timeout=15)
        original = (result.path / "run.simtrace").read_bytes()

        def forbidden(*args, **kwargs):
            pytest.fail("presentation save must not start a child")

        monkeypatch.setattr(coordinator, "submit", forbidden)
        patch = {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "visual": {"color": "red"}}}}
        changed = owner.write_layout(initial.revision, patch)
        assert changed.revision != initial.revision
        view = coordinator.store.presentation_view(revision=changed.revision)
        assert view.key == key and view.committed
        assert (result.path / "run.simtrace").read_bytes() == original
        assert json.loads((coordinator.project.root / coordinator.project.layout).read_text()) == patch
        assert owner.validate_layout(changed.revision, {"schema_version": "1.0"}).requires_rerun is False


def test_unseen_external_presentation_save_conflicts_without_spawning(tmp_path, monkeypatch):
    import json

    with Coordinator(project(tmp_path, PRESENTATION_MODEL)) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        key = coordinator.submit(state.request, state.revision, timeout=10)
        coordinator.wait(key, timeout=15)

        def forbidden(*args, **kwargs):
            pytest.fail("external presentation change must not start a child")

        monkeypatch.setattr(coordinator, "submit", forbidden)
        external = {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "visual": {"color": "blue"}}}}
        path = coordinator.project.root / coordinator.project.layout
        path.write_text(json.dumps(external))
        with pytest.raises(RevisionConflict, match="disk"):
            owner.write_layout(state.revision, {"schema_version": "1.0"})
        assert owner.state.revision != state.revision
        assert json.loads(path.read_text()) == external
        assert coordinator.store.presentation_view(revision=owner.state.revision).key == key


def test_invalid_external_layout_pauses_then_valid_save_recovers(tmp_path):
    with Coordinator(project(tmp_path)) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        path = coordinator.project.root / coordinator.project.layout
        path.write_bytes(b"invalid external")
        invalid = owner.observe_layout(state.revision)
        assert invalid.request is None and "layout" in invalid.issues
        with pytest.raises(ValueError, match="Layout must be refreshed"):
            owner.apply(invalid.revision, {"seed": 8}, auto_run=False)
        repaired = owner.write_layout(invalid.revision, {"schema_version": "1.0"}, auto_run=False)
        assert repaired.request is not None and not repaired.issues
        assert path.read_text().strip().startswith("{")


def test_source_refresh_keeps_revision_authoritative_and_preserves_inputs(tmp_path, monkeypatch):
    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        initial = owner.bind(coordinator.prepare(timeout=10))
        changed = owner.apply(initial.revision, {"parameters": {"n": 8}}, auto_run=False)
        (spec.root / "input.txt").write_text("10")
        original = coordinator._schedule

        def schedule(*args, **kwargs):
            assert owner.state.revision == coordinator.store.current_revision()
            assert owner.state.request is None
            return original(*args, **kwargs)

        monkeypatch.setattr(coordinator, "_schedule", schedule)
        refreshed = owner.refresh(changed.revision, auto_run=False, timeout=10)
        assert refreshed.request.parameters["n"] == 8
        assert refreshed.request.input_hash != changed.request.input_hash
        assert refreshed.revision == coordinator.store.current_revision()
        assert not refreshed.issues


def test_failed_source_refresh_leaves_current_invalid_revision(tmp_path):
    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        (spec.root / "plant.py").write_text("invalid Python syntax !!")
        with pytest.raises(ExecutionFailure):
            owner.refresh(state.revision, auto_run=False, timeout=10)
        assert owner.state.revision == coordinator.store.current_revision()
        assert owner.state.request is None and "source" in owner.state.issues
        with pytest.raises(ValueError, match="Source must be refreshed"):
            owner.apply(owner.state.revision, {"seed": 2}, auto_run=False)


@pytest.mark.parametrize("boundary", ["before_capture", "before_adoption"])
def test_superseded_source_refresh_cannot_overwrite_newer_success(tmp_path, monkeypatch, boundary):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        original = coordinator.prepare
        entered, release = threading.Event(), threading.Event()
        calls = []

        def prepare(**kwargs):
            calls.append(kwargs)
            first = len(calls) == 1
            if first and boundary == "before_capture":
                entered.set()
                assert release.wait(10)
            prepared = original(**kwargs)
            if first and boundary == "before_adoption":
                entered.set()
                assert release.wait(10)
            return prepared

        monkeypatch.setattr(coordinator, "prepare", prepare)
        with ThreadPoolExecutor(max_workers=1) as pool:
            old = pool.submit(owner.refresh, state.revision, auto_run=False, timeout=10)
            try:
                assert entered.wait(10)  # Same budget as the real preparation being fenced.
                (spec.root / "input.txt").write_text("12")
                newer = owner.refresh(owner.state.revision, auto_run=False, timeout=10)
            finally:
                release.set()
            with pytest.raises(RevisionConflict):
                old.result(timeout=10)
        assert owner.state == newer
        assert newer.request is not None and not newer.issues
        assert newer.revision == coordinator.store.current_revision()


def test_physical_layout_save_starts_one_fresh_attempt(tmp_path, monkeypatch):
    with Coordinator(project(tmp_path, PRESENTATION_MODEL)) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        original_key = coordinator.submit(state.request, state.revision, timeout=10)
        original_result = coordinator.wait(original_key, timeout=15)
        original = coordinator.submit
        submissions = []

        def submit(*args, **kwargs):
            key = original(*args, **kwargs)
            submissions.append(key)
            return key

        monkeypatch.setattr(coordinator, "submit", submit)
        physical = {"schema_version": "1.0", "grid": {"dx": 2}}
        validation = owner.validate_layout(state.revision, physical)
        assert validation.requires_rerun
        changed = owner.write_layout(state.revision, physical, timeout=10)
        assert len(submissions) == 1
        assert submissions[0].request_id != original_key.request_id
        result = coordinator.wait(submissions[0], timeout=15)
        assert result.key.attempt_id != original_result.key.attempt_id
        assert coordinator.store.presentation_view(revision=changed.revision).key == result.key
        assert owner.observe_layout(changed.revision) == changed
        assert len(submissions) == 1
        path = coordinator.project.root / coordinator.project.layout
        path.write_text('{"schema_version":"1.0","network":{"type":"explicit","nodes":[],"arcs":[]}}')
        external = owner.observe_layout(changed.revision, timeout=10)
        assert len(submissions) == 2
        coordinator.wait(submissions[1], timeout=15)
        assert coordinator.store.presentation_view(revision=external.revision).key == submissions[1]


@pytest.mark.parametrize("repair", ["write", "observe"])
def test_layout_can_be_repaired_after_source_refresh_hits_invalid_json(tmp_path, repair):
    from simulatte.layout.errors import LayoutError

    with Coordinator(project(tmp_path)) as coordinator:
        owner = coordinator.inputs
        initial = owner.bind(coordinator.prepare(timeout=10))
        path = coordinator.project.root / coordinator.project.layout
        path.write_text("invalid JSON")
        with pytest.raises(LayoutError):
            owner.refresh(initial.revision, timeout=10)
        assert "source" in owner.state.issues
        if repair == "write":
            fixed = owner.write_layout(owner.state.revision, {"schema_version": "1.0"}, timeout=10)
        else:
            path.write_text('{"schema_version":"1.0"}')
            fixed = owner.observe_layout(owner.state.revision, timeout=10)
        assert not fixed.issues and fixed.request is not None
        queued = [event for event in coordinator.events() if event["type"] == "queued"]
        from simulatte.execution.schema import AttemptKey

        result = coordinator.wait(AttemptKey(**queued[-1]["key"]), timeout=15)
        assert result.key.request_id == queued[-1]["key"]["request_id"]


def test_running_presentation_view_follows_original_attempt_to_publication(tmp_path):
    from tests.execution.test_coordinator import until_running

    model = PRESENTATION_MODEL.replace(
        "        yield env.timeout(1)", "        import time\n        time.sleep(3)\n        yield env.timeout(1)"
    )
    with Coordinator(project(tmp_path, model)) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        key = coordinator.submit(state.request, state.revision, timeout=10)
        until_running(coordinator, key)
        changed = owner.write_layout(
            state.revision,
            {"schema_version": "1.0", "placements": {"machine": {"kind": "server", "visual": {"label": "Live"}}}},
        )
        coordinator.store.require_active(key)
        provisional = coordinator.store.presentation_view(revision=changed.revision)
        assert provisional.key == key and not provisional.committed
        assert coordinator.store.request(key.request_id) == state.request
        coordinator.wait(key, timeout=15)
        assert coordinator.store.presentation_view(revision=changed.revision).committed


@pytest.mark.parametrize("boundary", ["directory_sync", "revision_commit"])
def test_save_failure_after_rename_retains_backup_and_pauses_execution(tmp_path, monkeypatch, boundary):
    from simulatte.execution import layout_files
    from simulatte.execution.store import _read
    import base64

    spec = project(tmp_path)
    path = spec.root / spec.layout
    original = b'{"schema_version":"1.0"}\n'
    path.write_bytes(original)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))

        def fail(*args, **kwargs):
            raise OSError("disk full at save boundary")

        if boundary == "directory_sync":
            monkeypatch.setattr(layout_files, "sync_directory", fail)
        else:
            create = coordinator.store.create_revision
            calls = []

            def fail_once(*args, **kwargs):
                calls.append(None)
                if len(calls) == 1:
                    fail()
                return create(*args, **kwargs)

            monkeypatch.setattr(coordinator.store, "create_revision", fail_once)
        with pytest.raises(OSError, match="disk full"):
            owner.write_layout(state.revision, {"schema_version": "1.0", "grid": {"dx": 2}}, auto_run=False)
        assert path.read_bytes() != original
        assert owner.state.request is None and "layout" in owner.state.issues
        with coordinator.store._transaction() as db:
            rows = db.execute("SELECT manifest FROM snapshots WHERE kind='layout-backup'").fetchall()
        assert len(rows) == 1
        assert base64.b64decode(_read(rows[0][0])["raw_base64"], validate=True) == original


def test_layout_aba_and_second_tab_cannot_write_with_old_revision(tmp_path):
    with Coordinator(project(tmp_path)) as coordinator:
        owner = coordinator.inputs
        first = owner.bind(coordinator.prepare(timeout=10))
        assert owner.write_layout(first.revision, {"schema_version": "1.0"}, auto_run=False) == first
        a = {"schema_version": "1.0", "grid": {"dx": 2}}
        b = {"schema_version": "1.0", "grid": {"dx": 3}}
        second = owner.write_layout(first.revision, a, auto_run=False)
        third = owner.write_layout(second.revision, b, auto_run=False)
        fourth = owner.write_layout(third.revision, a, auto_run=False)
        assert fourth.revision.digest == second.revision.digest
        assert fourth.revision.number != second.revision.number
        path = coordinator.project.root / coordinator.project.layout
        saved = path.read_bytes()
        with pytest.raises(RevisionConflict):
            owner.write_layout(second.revision, b, auto_run=False)
        assert path.read_bytes() == saved and owner.state == fourth


def test_presentation_validation_reply_is_fenced_after_concurrent_apply(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from simulatte.execution import inputs
    from tests.execution.test_revisions import layout_bundle

    with Coordinator(project(tmp_path)) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        key = coordinator.store.assign(state.request, state.revision)
        coordinator.store.transition(key, "preparing")
        coordinator.store.transition(key, "ready")
        coordinator.store.bind_view(state.revision, key, layout_bundle())
        entered, release = threading.Event(), threading.Event()
        original = inputs.reresolve_presentation

        def resolve(*args):
            entered.set()
            assert release.wait(5)
            return original(*args)

        monkeypatch.setattr(inputs, "reresolve_presentation", resolve)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(owner.validate_layout, state.revision, {"schema_version": "1.0"})
            try:
                assert entered.wait(5)
                current = owner.apply(state.revision, {"seed": 1}, auto_run=False)
            finally:
                release.set()
            with pytest.raises(RevisionConflict):
                pending.result(timeout=5)
        assert owner.state == current


def test_unbound_closed_and_failed_owners_refuse_mutations(tmp_path):
    coordinator = Coordinator(project(tmp_path))
    try:
        owner = coordinator.inputs
        base = coordinator.store.create_revision({})
        for call in (
            lambda: owner.apply(base, {}, auto_run=False),
            lambda: owner.validate_layout(base, {"schema_version": "1.0"}),
            lambda: owner.observe_layout(base),
            lambda: owner.invalidate(base, "changed"),
        ):
            with pytest.raises(RevisionConflict, match="metadata"):
                call()
        coordinator._fatal = ExecutionFailure(None, "failed", {"message": "store failed"})
        with pytest.raises(RuntimeError, match="cannot accept"):
            owner.apply(base, {})
    finally:
        coordinator.close()
    with pytest.raises(RuntimeError, match="cannot accept"):
        owner.apply(base, {})


def test_layout_edit_during_inspection_cannot_become_save_baseline(tmp_path):
    with Coordinator(project(tmp_path)) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        path = coordinator.project.root / coordinator.project.layout
        path.write_text('{"schema_version":"1.0","grid":{"dx":2}}')
        with pytest.raises(RevisionConflict, match="during preparation"):
            coordinator.inputs.bind(prepared)
        assert coordinator.inputs.state is None
        assert coordinator.store.current_revision() == prepared.revision


def test_external_edit_at_final_replace_check_is_adopted_not_overwritten(tmp_path, monkeypatch):
    from simulatte.execution import layout_files

    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        path = spec.root / spec.layout
        external = b'{"schema_version":"1.0","grid":{"dx":3}}'
        original = layout_files.StagedLayout.replace

        def editing(staged, **kwargs):
            path.write_bytes(external)
            return original(staged, **kwargs)

        monkeypatch.setattr(layout_files.StagedLayout, "replace", editing)
        with pytest.raises(RevisionConflict, match="disk"):
            owner.write_layout(state.revision, {"schema_version": "1.0", "grid": {"dx": 2}}, auto_run=False)
        assert path.read_bytes() == external
        assert owner.state.revision != state.revision
        assert owner.state.request.layout_hash != state.request.layout_hash


def test_unwritable_index_after_save_stops_all_new_admission(tmp_path, monkeypatch):
    with Coordinator(project(tmp_path)) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))

        def fail(*args, **kwargs):
            raise OSError("index unavailable")

        monkeypatch.setattr(coordinator.store, "create_revision", fail)
        with coordinator._condition:
            coordinator.submit(state.request, state.revision, timeout=10)
            with pytest.raises(OSError, match="index unavailable"):
                owner.write_layout(state.revision, {"schema_version": "1.0", "grid": {"dx": 2}}, auto_run=False)
            assert coordinator._pending is None
        assert coordinator._fatal.details["index_terminal_state"] == "not_durable"
        with pytest.raises(RuntimeError):
            owner.apply(state.revision, {"seed": 2})


def test_removed_parameter_and_changed_stopping_family_require_review(tmp_path):
    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        owner = coordinator.inputs
        initial = owner.bind(coordinator.prepare(timeout=10))
        model = (
            MODEL.replace("n: int = 2", "x: int = 3")
            .replace("params.n", "params.x")
            .replace("horizon=2", "stop=simulatte.FinitePopulation(arrivals=0, safety_limit=5)")
            .replace("    cell = Cell(env)", "    simulatte.Population(env)\n    cell = Cell(env)")
        )
        (spec.root / "plant.py").write_text(model)
        refreshed = owner.refresh(initial.revision, auto_run=False, timeout=10)
        assert refreshed.request is None
        assert "n" in refreshed.issues and "stopping" in refreshed.issues
        assert refreshed.options["stopping"]["type"] == "finite_population"
        with pytest.raises(ValueError, match="Unknown execution input"):
            owner.apply(refreshed.revision, {"unknown": 1}, auto_run=False)
        accepted = owner.apply(refreshed.revision, {}, auto_run=False)
        assert not accepted.issues and accepted.request.parameters["x"] == 3


@pytest.mark.parametrize("change", ["different_seed", "cancel_during_validation"])
def test_presentation_reuse_requires_equal_inputs_and_still_valid_attempt(tmp_path, monkeypatch, change):
    from dataclasses import replace
    from simulatte.execution import inputs
    from tests.execution.test_revisions import layout_bundle

    with Coordinator(project(tmp_path)) as coordinator:
        owner = coordinator.inputs
        state = owner.bind(coordinator.prepare(timeout=10))
        request = replace(state.request, seed=99) if change == "different_seed" else state.request
        key = coordinator.store.assign(request, state.revision)
        coordinator.store.transition(key, "preparing")
        coordinator.store.transition(key, "ready")
        coordinator.store.bind_view(state.revision, key, layout_bundle())
        if change == "cancel_during_validation":
            original = inputs.reresolve_presentation

            def cancel(*args):
                result = original(*args)
                coordinator.store.revoke(key, "cancelled")
                return result

            monkeypatch.setattr(inputs, "reresolve_presentation", cancel)
        validation = owner.validate_layout(state.revision, {"schema_version": "1.0"})
        assert validation.requires_rerun and validation.bundle is None


def test_input_owner_preserves_full_63_bit_seed_without_json_precision_loss(tmp_path):
    with Coordinator(project(tmp_path)) as coordinator:
        owner = coordinator.inputs
        initial = owner.bind(coordinator.prepare(timeout=10))
        for seed in (2**63 - 1, "9223372036854775807"):
            changed = owner.apply(owner.state.revision, {"seed": seed}, auto_run=False)
            assert changed.options["seed"] == "9223372036854775807"
            assert changed.request.seed == 2**63 - 1
        for seed in ("01", "-1", "1.0", "9223372036854775808", True):
            with pytest.raises(ValueError):
                owner.apply(changed.revision, {"seed": seed}, auto_run=False)
        assert owner.state == changed and initial.revision != changed.revision


def test_initial_options_preserve_partial_parameters_without_execution(tmp_path):
    source = MODEL.replace("n: int = 2", "n: int\n    label: str")
    with Coordinator(project(tmp_path, source)) as coordinator:
        prepared = coordinator.prepare(timeout=10)
        base = coordinator.store.current_revision()
        for initial in ({"parameters": {"n": "bad"}}, {"unknown": True}, {"seed": -1}, {"parameters": {"unknown": 1}}):
            with pytest.raises(ValueError):
                coordinator.inputs.bind(prepared, initial=initial)
        assert coordinator.inputs.state is None and coordinator.store.current_revision() == base
        state = coordinator.inputs.bind(prepared, initial={"parameters": {"n": 8}, "seed": 12})
        assert state.options["parameters"] == {"n": 8}
        assert state.options["seed"] == "12"
        assert set(state.issues) == {"label"} and state.request is None
        with pytest.raises(ValueError, match="initial"):
            coordinator.inputs.bind(coordinator.inputs.captured_inputs()[1], initial={})
        applied = coordinator.inputs.apply(state.revision, {"parameters": {"n": 8, "label": "ok"}}, auto_run=False)
        assert applied.request.seed == 12 and applied.request.parameters["label"] == "ok"


def test_startup_override_error_survives_source_refresh_until_apply(tmp_path):
    spec = project(tmp_path)
    with Coordinator(spec) as coordinator:
        state = coordinator.inputs.bind(coordinator.prepare(timeout=10), initial_error="Review invalid CLI override")
        assert state.request is None and "startup" in state.issues
        (spec.root / "plant.py").write_text(MODEL + "\n# source edit\n")
        state = coordinator.inputs.refresh(state.revision, auto_run=False, timeout=10)
        assert state.request is None and "startup" in state.issues
        accepted = coordinator.inputs.apply(state.revision, {}, auto_run=False)
        assert accepted.request is not None and not accepted.issues
