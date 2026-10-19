"""Preparation failures preserve ownership, evidence and the accepted lockfile."""

from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from simulatte.execution.store import RunStore
from simulatte.projects.environments import EnvironmentPool
from simulatte.projects import install_starter, starter_files, load_workspace
from tests.execution.test_project_environments import configured as configured, fake_pool


def test_cancelled_operation_never_launches_and_active_cancel_is_contained(configured, monkeypatch):
    from simulatte.projects import environments

    with RunStore.open(configured.root / ".simulatte") as store:
        pool = EnvironmentPool(configured.root, store)
        pool.cancel()
        with monkeypatch.context() as patch:
            patch.setattr(
                environments.ContainedProcess, "start", lambda *a, **kw: pytest.fail("cancelled operation launched")
            )
            with pytest.raises(ValueError, match="cancelled"):
                pool._run([sys.executable], cwd=configured.root)
        pool._cancelled.clear()
        outcome = []

        def run():
            try:
                pool._run(
                    [sys.executable, "-u", "-c", 'import time; print("ready"); time.sleep(30)'], cwd=configured.root
                )
            except ValueError as error:
                outcome.append(str(error))

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 10
        while "ready" not in pool.progress and thread.is_alive():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        pool.cancel()
        thread.join(10)
        assert not thread.is_alive()
        assert outcome and "cancelled" in outcome[0]


@pytest.mark.parametrize("failure", ["exit", "json", "os", "timeout"])
@pytest.mark.parametrize("installation", [False, True])
def test_interpreter_probe_errors_are_bounded(monkeypatch, failure, installation):
    from simulatte.projects import environments

    def run(*args, **kw):
        if failure == "os":
            raise OSError("missing")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("probe", 20)
        return SimpleNamespace(returncode=int(failure == "exit"), stderr=b"x" * 9000, stdout=b"bad")

    monkeypatch.setattr(environments.subprocess, "run", run)
    with pytest.raises(ValueError) as caught:
        if installation:
            EnvironmentPool._probe_installation(Path("missing"), {})
        else:
            EnvironmentPool._probe(Path("missing"))
    assert len(str(caught.value)) < 5000


def test_environment_inputs_are_explicit_and_contained(tmp_path, monkeypatch):
    from simulatte.projects import environments
    from uuid import uuid4
    from simulatte.projects import WorkspaceDefinition

    workspace = WorkspaceDefinition(tmp_path, str(uuid4()), (), (), None)
    with RunStore.open(tmp_path / ".simulatte") as store:
        pool = EnvironmentPool(tmp_path, store)
        with pytest.raises(ValueError, match="Configure"):
            pool._files(workspace)
        install_starter(tmp_path, starter_files("production"))
        workspace = load_workspace(tmp_path)
        files = pool._files(workspace)
        assert "models/production.py" in files
        assert not any(name.startswith(".simulatte/") for name in files)
        outside = tmp_path.parent / (tmp_path.name + "-outside")
        outside.write_text("external")
        (tmp_path / "uv.lock").symlink_to(outside)
        with pytest.raises(ValueError, match="escapes"):
            pool._files(replace(workspace, models=()))
        monkeypatch.setattr(environments.shutil, "which", lambda name: None)
        with pytest.raises(ValueError, match="uv is missing"):
            pool._uv()
        monkeypatch.setattr(environments.shutil, "which", lambda name: "/test/uv")
        assert pool._uv() == "/test/uv"


@pytest.mark.parametrize("failure", ["lock-changed", "probe-different", "symlink", "owned", "lock-wait"])
def test_failed_generation_is_not_published(configured, monkeypatch, failure):
    from simulatte.projects import environments

    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        generation = pool.prepare(configured)
        from simulatte.execution.schema import _plain

        spec = _plain(generation.evidence["spec"])
        pool.cleanup(generation.id)
        if failure == "symlink":
            (pool.cache / generation.id).symlink_to(configured.root, target_is_directory=True)
        elif failure == "owned":
            with store._transaction() as db:
                db.execute(
                    'INSERT INTO "references" VALUES (?,?,?,?)',
                    ("environment-owner", "active", "environment", generation.id),
                )
        elif failure == "lock-wait":
            monkeypatch.setattr(environments, "_acquire", lambda f: (_ for _ in ()).throw(OSError("busy")))
            pool.cancel()
        else:
            original = pool._run

            def run(command, *, cwd, environment=None):
                original(command, cwd=cwd, environment=environment)
                if failure == "lock-changed":
                    (cwd / "uv.lock").write_text("changed")

            monkeypatch.setattr(pool, "_run", run)
        with pytest.raises(ValueError, match="changed|differs|symlink|owned|cancelled"):
            pool._prepare_spec(
                spec,
                expected_probe={**_plain(generation.evidence["probe"]), "installation": {"format": "installation-v1"}}
                if failure == "probe-different"
                else None,
            )
        assert pool.selected is None
        if failure != "symlink":
            assert not (pool.cache / generation.id / "published").exists()


def test_generation_leases_and_cleanup_refuse_wrong_ownership(configured, monkeypatch):
    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        first = pool.prepare(configured)
        (configured.root / "uv.lock").write_text("version = 2\n")
        second = pool.prepare(configured)
        pool.acquire(first.id, "run")
        with pytest.raises(ValueError, match="different generation"):
            pool.acquire(second.id, "run")
        with pytest.raises(ValueError, match="busy"):
            pool.activate(second.id)
        pool.release("run")
        pool.activate(first.id)
        assert pool.select(first.id) == "selected"
        with pytest.raises(ValueError, match="selected"):
            pool.cleanup(first.id)
        existing = pool.use_existing(Path(sys.executable))
        with pytest.raises(ValueError, match="outside"):
            pool.cleanup(existing.id)
        with pytest.raises(KeyError):
            pool.get("0" * 64)
        spec = dict(first.evidence["spec"])
        spec["platform"] = "unavailable-platform"
        with pytest.raises(ValueError, match="unavailable"):
            pool.reconstruct({"spec": spec, "probe": dict(first.evidence["probe"])})
        # Changed evidence for an existing identity cannot silently overwrite history.
        from simulatte.execution.schema import _plain

        changed = _plain(first.evidence)
        changed["probe"]["installation"]["facts"]["python"] = "changed"
        with pytest.raises(ValueError, match="evidence changed"):
            pool._save(first.id, changed)


def test_native_command_failure_preserves_diagnostics(configured):
    with RunStore.open(configured.root / ".simulatte") as store:
        pool = EnvironmentPool(configured.root, store)
        with pytest.raises(ValueError, match="failed"):
            pool._run(
                [sys.executable, "-c", 'import sys; print("dependency unavailable", flush=True); sys.exit(2)'],
                cwd=configured.root,
                environment=configured.root / "new-env",
            )
        assert "dependency unavailable" in pool.progress
