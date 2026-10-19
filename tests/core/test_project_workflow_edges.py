"""Integration edges found while following the documented project workflow."""

from dataclasses import asdict

from simulatte.projects import StudioTarget, install_starter, starter_files, load_workspace
from simulatte.projects.service import ProjectService
import pytest


def test_registration_keeps_default_until_its_model_is_removed(tmp_path):
    install_starter(tmp_path, starter_files("production"))
    service = ProjectService.open(StudioTarget(tmp_path, None))
    try:
        models = [asdict(m) for m in service.workspace.models]
        preview = service.configure({"models": models})
        service.configure({"apply": preview["preview_id"]})
        assert load_workspace(tmp_path).default_model == "production"
        preview = service.configure({"models": []})
        service.configure({"apply": preview["preview_id"]})
        assert load_workspace(tmp_path).default_model is None
    finally:
        service.close()


def test_workspace_timeout_bounds_admitted_work(tmp_path):
    import pytest
    from simulatte.execution.coordinator import ExecutionFailure
    from simulatte.execution.store import RunStore
    from tests.execution.test_project_execution import project, coordinator

    workspace = project(tmp_path, hanging=True)
    model = workspace.models[0].id
    with RunStore.open(tmp_path / ".simulatte") as store, coordinator(workspace, store) as co:
        prepared = co.prepare_model(model, timeout=10)
        started = co.admit_run(model, co.make_request(prepared), prepared.revision, timeout=0.5)
        with pytest.raises(ExecutionFailure) as caught:
            co.wait(started.key, timeout=10)
        assert caught.value.details["code"] == "timeout"
        with pytest.raises(KeyError):
            store.result(started.key.attempt_id)


def test_legacy_layout_and_timeout_flags_remain_explicit_setup_data(tmp_path):
    (tmp_path / "plant.py").write_text('raise AssertionError("must not import")')
    service = ProjectService.open(StudioTarget(tmp_path, str(tmp_path / "plant.py") + ":alternate"))
    try:

        def options(prepared):
            raise AssertionError("must not inspect")

        options.flags = {"layout": "layouts/custom.json", "timeout": 12}
        service.initial_options = options
        state = service.state()
        assert state["legacy_layout"] == "layouts/custom.json"
        assert service.timeout == 12
        assert service.coordinator is None
    finally:
        service.close()


@pytest.mark.parametrize(
    "change", [{"key": ""}, {"name": " "}, {"entrypoint": "plant.py:for"}, {"entrypoint": "plant.txt:build"}]
)
def test_registration_rejects_invalid_display_and_callable_fields(tmp_path, change):
    from dataclasses import replace
    from simulatte.projects import preview_config
    from tests.core.test_projects import definition

    workspace = definition(tmp_path)
    with pytest.raises(ValueError):
        preview_config(tmp_path, replace(workspace, models=(replace(workspace.models[0], **change),)))


def test_analysis_registration_and_invalid_declarations_are_data_only(tmp_path):
    from dataclasses import replace
    from uuid import uuid4
    from simulatte.projects import AnalysisRegistration, preview_config, apply_config
    from tests.core.test_projects import definition

    workspace = replace(
        definition(tmp_path), analyses=(AnalysisRegistration(str(uuid4()), "summary", "Summary", "summary.py:analyze"),)
    )
    apply_config(preview_config(tmp_path, workspace))
    assert load_workspace(tmp_path).analyses == workspace.analyses
    (tmp_path / "pyproject.toml").write_text("[tool.simulatte.project]\nschema=1\n")
    with pytest.raises(ValueError, match="declaration"):
        load_workspace(tmp_path)


def test_config_apply_refuses_change_during_atomic_write(tmp_path, monkeypatch):
    from simulatte.projects import preview_config, apply_config
    from simulatte.projects import config
    from tests.core.test_projects import definition

    path = tmp_path / "pyproject.toml"
    path.write_text("# original\n")
    preview = preview_config(tmp_path, definition(tmp_path))
    original = config.os.fsync

    def changed(fd):
        path.write_text("# concurrent user edit\n")
        original(fd)

    monkeypatch.setattr(config.os, "fsync", changed)
    with pytest.raises(ValueError, match="during apply"):
        apply_config(preview)
    assert path.read_text() == "# concurrent user edit\n"
    assert not list(tmp_path.glob("*.lock"))


def test_starters_and_config_reject_symlink_destinations(tmp_path):
    from simulatte.projects import preview_config
    from dataclasses import replace
    from tests.core.test_projects import definition

    with pytest.raises(ValueError, match="Unknown"):
        starter_files("unknown")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "models").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        install_starter(tmp_path, {"models/new.py": b"source"})
    (tmp_path / "config.toml").write_text("# existing config\n")
    (tmp_path / "pyproject.toml").symlink_to(tmp_path / "config.toml")
    assert preview_config(tmp_path, definition(tmp_path)).original_sha256
    with pytest.raises(ValueError, match="root"):
        preview_config(tmp_path, replace(definition(tmp_path), root=outside))
