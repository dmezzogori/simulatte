"""Managed generations never mutate an environment owned by captured work."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import importlib.util
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from simulatte.execution.store import RunStore
from simulatte.projects import WorkspaceDefinition


def api():
    assert importlib.util.find_spec("simulatte.projects.environments"), "environment generations missing"
    return importlib.import_module("simulatte.projects.environments")


@pytest.fixture
def configured(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="test"\nversion="0.1"\nrequires-python=">=3.11"\ndependencies=[]\n'
    )
    (tmp_path / "uv.lock").write_text("version = 1\n")
    return WorkspaceDefinition(tmp_path, str(uuid4()), (), (), None)


def fake_pool(workspace, store, monkeypatch):
    m = api()
    pool = m.EnvironmentPool(workspace.root, store)

    def run(command, *, cwd, environment=None):
        if "lock" in command:
            (cwd / "uv.lock").write_text("version = 2\n")
        elif "sync" in command:
            interpreter = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            interpreter.parent.mkdir(parents=True)
            interpreter.write_bytes(b"captured interpreter")

    monkeypatch.setattr(pool, "_run", run)
    monkeypatch.setattr(pool, "_uv", lambda: "uv")
    monkeypatch.setattr(
        pool,
        "_probe",
        lambda path: {
            "runtime": {
                "facts": {"python": "3.14.3", "simulatte": "0.17.0"},
                "dependencies": {"packages": {}},
                "warnings": [],
            },
            "distribution_roots": [],
            "studio": True,
        },
    )
    monkeypatch.setattr(
        pool,
        "_probe_installation",
        lambda path, bindings: {
            **pool._probe(path),
            "installation": {"format": "installation-v1", **pool._probe(path)["runtime"]},
            "bindings": bindings,
        },
    )
    return pool


def test_concurrent_prepares_converge_without_changing_lock(configured, monkeypatch):
    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        before = (configured.root / "uv.lock").read_bytes()
        with ThreadPoolExecutor(2) as executor:
            generations = list(executor.map(lambda _: pool.prepare(configured), range(2)))
        assert generations[0].id == generations[1].id
        assert generations[0].interpreter.read_bytes() == b"captured interpreter"
        assert (configured.root / "uv.lock").read_bytes() == before
        assert pool.selected is None


def test_generation_activation_waits_for_owners(configured, monkeypatch):
    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        old = pool.prepare(configured)
        assert pool.select(old.id) == "restart_required"
        pool.activate(old.id)
        for owner in ("simulation:1", "analysis:1", "experiment:1"):
            pool.acquire(old.id, owner)
        (configured.root / "uv.lock").write_text("version = 2\n")
        new = pool.prepare(configured)
        assert old.id != new.id
        assert pool.select(new.id) == "busy"
        assert pool.selected.id == old.id
        with pytest.raises(ValueError, match="owned"):
            pool.cleanup(old.id)
        for owner in ("simulation:1", "analysis:1"):
            pool.release(owner)
            assert pool.select(new.id) == "busy"
        pool.release("experiment:1")
        assert pool.select(new.id) == "restart_required"
        assert old.interpreter.read_bytes() == b"captured interpreter"
        assert pool.get(old.id).evidence == old.evidence
        pool.activate(new.id)
        pool.cleanup(old.id)
        assert pool.get(old.id).evidence == old.evidence
        restored = pool.reconstruct(old.evidence)
        assert restored.id == old.id and restored.interpreter.exists()
        assert pool.selected.id == new.id


def test_failure_cancellation_and_lock_preview(configured, monkeypatch):
    m = api()
    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        with pytest.raises(m.LockPreviewRequired) as caught:
            pool.prepare(configured, lock_update=True)
        assert caught.value.edit.proposed_bytes == b"version = 2\n"
        assert (configured.root / "uv.lock").read_bytes() == b"version = 1\n"

        def fail(*args, **kwargs):
            raise RuntimeError("cancelled")

        monkeypatch.setattr(pool, "_run", fail)
        with pytest.raises(RuntimeError, match="cancelled"):
            pool.prepare(configured)
        assert not list((configured.root / ".simulatte/cache/environments").glob("*/published"))
        assert pool.selected is None


def test_existing_environment_drift_and_reconstruction_failures(configured, monkeypatch):
    m = api()
    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        existing = pool.use_existing(Path(sys.executable))
        pool.activate(existing.id)
        assert pool.acquire(existing.id, "experiment:resume").id == existing.id
        reloaded = m.EnvironmentPool(configured.root, store)
        monkeypatch.setattr(reloaded, "_probe", pool._probe)
        assert reloaded.get(existing.id).evidence == existing.evidence
        assert reloaded.owners(existing.id) == ("experiment:resume",)
        monkeypatch.setattr(pool, "_probe", lambda p: {"changed": True})
        with pytest.raises(ValueError, match="changed"):
            pool.acquire(existing.id, "simulation:new")
        with pytest.raises(ValueError, match="unavailable"):
            pool.reconstruct(existing.evidence)
        assert pool.selected.id == existing.id


def test_native_process_output_and_cancellation(configured):
    m = api()
    with RunStore.open(configured.root / ".simulatte") as store:
        pool = m.EnvironmentPool(configured.root, store)
        pool._run([sys.executable, "-c", 'print("prepared", flush=True)'], cwd=configured.root)
        assert "prepared" in pool.progress
        pool.cancel()
        with pytest.raises(ValueError, match="cancelled"):
            pool._run([sys.executable, "-c", "import time; time.sleep(30)"], cwd=configured.root)


def test_existing_native_environment_probe_does_not_install(configured):
    m = api()
    with RunStore.open(configured.root / ".simulatte") as store:
        pool = m.EnvironmentPool(configured.root, store)
        generation = pool.use_existing(Path(sys.executable))
        assert generation.evidence["probe"]["runtime"]["facts"]["python"]
        assert pool.acquire(generation.id, "inspection:1").id == generation.id
        assert not pool.cache.exists()
        pool.release("inspection:1")


def test_new_generation_reconstructs_installation_metadata_without_rewriting_capture(configured, monkeypatch):
    from simulatte.execution.schema import _plain
    from simulatte.execution.hashing import canonical_hash
    import shutil

    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        raw = [1]

        def probe(path, bindings):
            return {
                **pool._probe(path),
                "runtime": {"install": raw[0]},
                "installation": {
                    "format": "installation-v1",
                    "facts": {"python": "3.14.3"},
                    "dependencies": {},
                    "warnings": [],
                },
                "bindings": bindings,
            }

        monkeypatch.setattr(pool, "_probe_installation", probe, raising=False)
        captured = pool.prepare(configured)
        evidence = _plain(captured.evidence)
        assert evidence["spec"]["runtime_format"] == "installation-v1"
        assert captured.id == canonical_hash("local-environment-v2", evidence["spec"])
        other = pool.use_existing(Path(sys.executable))
        pool.activate(other.id)
        shutil.rmtree(captured.interpreter.parent.parent)
        raw[0] = 2
        restored = pool.reconstruct(evidence)
        assert restored.id == captured.id and _plain(restored.evidence) == evidence
        assert pool.selected.id == other.id
        assert pool.installation_probe(restored)["runtime"] == {"install": 2}
        assert pool.acquire(restored.id, "test").id == restored.id
        pool.release("test")
        assert evidence["probe"]["runtime"] == {"install": 1}


def test_legacy_managed_generation_keeps_id_and_strict_raw_refusal(configured, monkeypatch):
    from simulatte.execution.schema import _plain
    from simulatte.execution.hashing import canonical_hash
    import shutil

    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        spec = _plain(pool.prepare(configured).evidence["spec"])
        del spec["runtime_format"]
        legacy = pool._prepare_spec(spec)
        assert legacy.id == canonical_hash("local-environment-v1", spec)
        assert "installation" not in legacy.evidence["probe"]
        shutil.rmtree(legacy.interpreter.parent.parent)
        raw = _plain(legacy.evidence["probe"])
        raw["runtime"]["dependencies"] = {"changed_record": True}
        monkeypatch.setattr(pool, "_probe", lambda path: raw)
        with pytest.raises(ValueError, match="differs"):
            pool.reconstruct(legacy.evidence)
        assert pool.get(legacy.id).evidence == legacy.evidence
        assert pool.selected is None


def test_evidence_tags_and_local_binding_document_cannot_downgrade(configured, monkeypatch):
    import json
    from simulatte.execution.schema import _plain

    m = api()
    for spec in (
        {"kind": "managed", "runtime_format": "future"},
        {"kind": "existing", "runtime_format": "installation-v1"},
    ):
        with pytest.raises(ValueError, match="format"):
            m.environment_identity(spec)
    for spec, probe in (({}, {"installation": {}}), ({"runtime_format": "installation-v1"}, {})):
        with pytest.raises(ValueError, match="format"):
            m._probe_identity(spec, probe)
    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        generation = pool.prepare(configured)
        local = pool.cache / generation.id / "installation.json"
        value = json.loads(local.read_text())
        value["installation"]["facts"]["python"] = "changed"
        local.write_text(json.dumps(value))
        with pytest.raises(ValueError, match="differs"):
            pool.installation_bindings(generation)
        assert pool.get(generation.id).evidence == generation.evidence
        assert m._captured_runtime({}, _plain(generation.evidence)["probe"]) == value["runtime"]


def test_new_reconstruction_requests_the_captured_exact_interpreter(configured, monkeypatch):
    import shutil
    from simulatte.execution.schema import _plain

    with RunStore.open(configured.root / ".simulatte") as store:
        pool = fake_pool(configured, store, monkeypatch)
        generation = pool.prepare(configured)
        shutil.rmtree(generation.interpreter.parent.parent)
        commands = []
        run = pool._run

        def capture(command, **kw):
            commands.append(command)
            run(command, **kw)

        monkeypatch.setattr(pool, "_run", capture)
        restored = pool.reconstruct(generation.evidence)
        assert commands[0][-2:] == ["--python", "cpython@3.14.3"]
        assert restored.id == generation.id
        assert _plain(restored.evidence) == _plain(generation.evidence)
