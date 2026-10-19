"""Immutable local environments with durable evidence and explicit owner leases."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from typing import Any, Literal

from simulatte._wire import freeze
from simulatte.execution.durability import replace_durable, sync_directory
from simulatte.execution.hashing import canonical_hash
from simulatte.execution.process import ContainedProcess
from simulatte.execution.provisioning import remove_tree
from simulatte.execution.schema import _hash, _plain, _mapping
from simulatte.execution.store import RunStore, _acquire, _release, _blob, _read, _directory
from simulatte.projects.types import ConfigEdit, WorkspaceDefinition


def environment_identity(spec):
    version = spec.get("runtime_format")
    if version not in (None, "installation-v1") or (version and spec["kind"] != "managed"):
        raise ValueError("Unsupported environment runtime evidence format")
    return canonical_hash("local-environment-v2" if version else "local-environment-v1", spec)


def _probe_identity(spec, probe):
    if spec.get("runtime_format") == "installation-v1":
        if probe.get("installation", {}).get("format") != "installation-v1":
            raise ValueError("Environment runtime evidence format mismatch")
        return {name: probe[name] for name in ("installation", "distribution_roots", "studio")}
    if "installation" in probe:
        raise ValueError("Environment runtime evidence format mismatch")
    return probe


def _captured_runtime(spec, probe):
    return _probe_identity(spec, probe) if spec.get("runtime_format") else probe["runtime"]


@dataclass(frozen=True, slots=True)
class EnvironmentGeneration:
    id: str
    interpreter: Path
    evidence: Mapping[str, Any]
    state: Literal["prepared", "selected", "retired"]


class LockPreviewRequired(ValueError):
    def __init__(self, edit: ConfigEdit):
        super().__init__("Review and apply the proposed uv.lock before preparing this environment")
        self.edit = edit


class EnvironmentPool:
    def __init__(self, root: Path, store: RunStore):
        self.root = root.resolve()
        self.store = store
        self.cache = store.root / "cache/environments"
        self._mutex = threading.RLock()
        self._cancelled = threading.Event()
        self.progress = ""

    def _uv(self):
        executable = shutil.which("uv")
        if executable is None:
            raise ValueError("uv is missing; install uv, then prepare the project again")
        return executable

    def cancel(self):
        self._cancelled.set()

    def _run(self, command, *, cwd, environment=None):
        if self._cancelled.is_set():
            raise ValueError("Environment preparation cancelled before starting")
        env = dict(os.environ)
        if environment is not None:
            env["UV_PROJECT_ENVIRONMENT"] = str(environment)
        child = ContainedProcess.start(command, cwd=cwd, env=env)
        tail = bytearray()

        def drain(stream):
            while data := stream.read(65536):
                tail.extend(data)
                del tail[:-8192]
                self.progress = bytes(tail).decode("utf-8", errors="replace")

        threads = [
            threading.Thread(target=drain, args=(stream,), daemon=True) for stream in (child.stdout, child.stderr)
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 600
        try:
            while child.returncode is None:
                if self._cancelled.is_set() or time.monotonic() >= deadline:
                    child.kill()
                    child.wait(timeout=5)
                    raise ValueError("Environment preparation cancelled or timed out")
                try:
                    child.wait(timeout=0.05)
                except TimeoutError:
                    pass
            if child.returncode:
                raise ValueError("Environment preparation failed: " + bytes(tail).decode("utf-8", errors="replace"))
        finally:
            child.close()
            for thread in threads:
                thread.join(5)

    @staticmethod
    def _probe(interpreter):
        # -I disables cwd/PYTHONPATH injection. No model or project package is imported.
        code = """import json,sysconfig,importlib.util
from simulatte.execution.runtime import describe_runtime
roots=list(dict.fromkeys(sysconfig.get_path(k) for k in ('purelib','platlib')))
print(json.dumps({'runtime':describe_runtime(roots),'distribution_roots':roots,
    'studio':all(importlib.util.find_spec(k) is not None for k in ('starlette','uvicorn','tomlkit'))}))
"""
        try:
            result = subprocess.run([str(interpreter), "-I", "-c", code], capture_output=True, timeout=20)
            if result.returncode:
                raise ValueError(result.stderr[-4096:].decode("utf-8", errors="replace"))
            return json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise ValueError(f"Environment interpreter unavailable: {error}") from error

    @staticmethod
    def _probe_installation(interpreter, bindings):
        code = """import json,sys,sysconfig,importlib.util
from simulatte.execution.runtime import describe_runtime
from simulatte.execution.installation import describe_installation
roots=list(dict.fromkeys(sysconfig.get_path(k) for k in ('purelib','platlib')))
bindings=json.load(sys.stdin)
print(json.dumps({'runtime':describe_runtime(roots),'distribution_roots':roots,
 'installation':describe_installation(roots,bindings=bindings),'bindings':bindings,
 'studio':all(importlib.util.find_spec(k) is not None for k in ('starlette','uvicorn','tomlkit'))}))
"""
        try:
            result = subprocess.run(
                [str(interpreter), "-I", "-c", code],
                input=json.dumps(bindings).encode(),
                capture_output=True,
                timeout=30,
            )
            if result.returncode:
                raise ValueError(
                    "Installation probe failed: " + result.stderr[-4096:].decode("utf-8", errors="replace")
                )
            return json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise ValueError(f"Installation interpreter unavailable: {error}") from error

    def installation_probe(self, generation):
        """Read this installation's verified-source bindings; never infer them from installed URLs."""
        return self._probe_installation(generation.interpreter, self.installation_bindings(generation))

    def installation_bindings(self, generation):
        captured = json.loads((self.cache / generation.id / "installation.json").read_text())
        if _probe_identity(generation.evidence["spec"], captured) != _probe_identity(
            generation.evidence["spec"], _plain(generation.evidence)["probe"]
        ):
            raise ValueError("Local installation evidence differs from captured generation")
        return captured["bindings"]

    def _files(self, workspace):
        files = {}
        if workspace.models:
            from simulatte.execution.snapshot import capture_source, discover_project
            import tarfile

            model = workspace.models[0]
            source = capture_source(
                discover_project(self.root / model.entrypoint.split(":")[0], self.root, layout=model.layout)
            )
            with tarfile.open(source.archive) as archive:
                for member in archive:
                    stream = archive.extractfile(member)
                    assert stream is not None
                    with stream:
                        files[member.name] = stream.read()
        for name in ("pyproject.toml", "uv.lock", ".python-version", "uv.toml"):
            path = self.root / name
            if path.exists():
                if not path.resolve().is_relative_to(self.root):
                    raise ValueError("Provisioning input escapes project")
                files[name] = path.read_bytes()
        if "pyproject.toml" not in files:
            raise ValueError("Configure pyproject.toml before preparing an environment")
        return files

    @staticmethod
    def _write_files(root, files):
        from simulatte.execution.snapshot import _path

        for name, data in files.items():
            _path(name)
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bytes.fromhex(data) if isinstance(data, str) else data)

    def preview_lock(self, workspace):
        files = self._files(workspace)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_files(root, files)
            self._run([self._uv(), "lock"], cwd=root)
            proposed = (root / "uv.lock").read_bytes()
        old = files.get("uv.lock")
        return ConfigEdit(self.root / "uv.lock", None if old is None else hashlib.sha256(old).hexdigest(), proposed)

    def prepare(self, workspace: WorkspaceDefinition, *, lock_update=False, reset_cancel=True):
        with self._mutex:
            # ProjectService establishes its operation before dispatch so early cancel is retained.
            if reset_cancel:
                self._cancelled.clear()
            if lock_update or not (self.root / "uv.lock").is_file():
                raise LockPreviewRequired(self.preview_lock(workspace))
            files = self._files(workspace)
            config = tomllib.loads(files["pyproject.toml"].decode())
            spec = {
                "kind": "managed",
                "runtime_format": "installation-v1",
                "files": {name: data.hex() for name, data in files.items()},
                "python": config.get("project", {}).get("requires-python", ">=3.11"),
                "platform": sys.platform,
                "architecture": platform.machine(),
                "implementation": sys.implementation.name,
                "provisioning": ["sync", "--locked", "--no-install-project", "--no-editable"],
            }
            return self._prepare_spec(spec)

    def _prepare_spec(self, spec, *, expected_probe=None):
        identity = environment_identity(spec)
        _directory(self.cache)
        directory = self.cache / identity
        lock_path = self.cache / (identity + ".lock")
        if lock_path.is_symlink() or directory.is_symlink():
            raise ValueError("Environment cache path is a symlink")
        with lock_path.open("a+b") as lock:
            deadline = time.monotonic() + 600
            while True:
                try:
                    _acquire(lock)
                    break
                except OSError:
                    if self._cancelled.is_set() or time.monotonic() >= deadline:
                        raise ValueError("Environment preparation cancelled while waiting for lock") from None
                    time.sleep(0.05)
            try:
                if (directory / "published").is_file():
                    found = self.get(identity)
                    self._verify(found)
                    return found
                if self.owners(identity):
                    raise ValueError("Cannot rebuild an owned generation")
                remove_tree(directory)
                try:
                    with tempfile.TemporaryDirectory(dir=self.cache, prefix="build-") as temporary:
                        build = Path(temporary)
                        self._write_files(build, spec["files"])
                        bindings = None
                        python = spec["python"]
                        if spec.get("runtime_format") == "installation-v1":
                            from simulatte.execution.installation import bind_artifacts

                            bindings = bind_artifacts(spec["files"], build)
                            if expected_probe is not None:
                                python = (
                                    spec["implementation"] + "@" + expected_probe["installation"]["facts"]["python"]
                                )
                        self._run(
                            [self._uv(), *spec["provisioning"], "--python", python],
                            cwd=build,
                            environment=directory,
                        )
                        if (build / "uv.lock").read_bytes() != bytes.fromhex(spec["files"]["uv.lock"]):
                            raise ValueError("Locked provisioning changed uv.lock")
                    interpreter = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    probe = (
                        self._probe(interpreter)
                        if bindings is None
                        else self._probe_installation(interpreter, bindings)
                    )
                    probe["distribution_roots"] = [
                        str(Path(p).relative_to(directory)) for p in probe["distribution_roots"]
                    ]
                    if expected_probe is not None and _captured_runtime(spec, probe) != _captured_runtime(
                        spec, expected_probe
                    ):
                        raise ValueError("Reconstructed runtime differs from captured environment")
                    evidence = {"spec": spec, "probe": probe}
                    self._save(identity, evidence)
                    if bindings is not None:
                        probe_path = directory / "installation.tmp"
                        probe_path.write_text(json.dumps(probe, sort_keys=True))
                        replace_durable(probe_path, directory / "installation.json")
                    marker = directory / "published.tmp"
                    marker.write_text(identity)
                    replace_durable(marker, directory / "published")
                    sync_directory(directory)
                except BaseException:
                    if not self.owners(identity):
                        remove_tree(directory)
                    raise
            finally:
                _release(lock)
        return self.get(identity)

    def _save(self, identity, evidence):
        with self.store._transaction() as db:
            old = db.execute("SELECT evidence FROM environment_generations WHERE id=?", (identity,)).fetchone()
            if old is not None:
                captured = _read(old[0])
                if _captured_runtime(captured["spec"], captured["probe"]) != _captured_runtime(
                    evidence["spec"], evidence["probe"]
                ):
                    raise ValueError("Environment evidence changed; reconstruction unavailable")
            db.execute(
                "INSERT OR IGNORE INTO environment_generations VALUES (?,?,?)", (identity, _blob(evidence), "prepared")
            )

    def get(self, identity):
        _hash(identity)
        with self.store._mutex:
            assert self.store._db is not None
            row = self.store._db.execute("SELECT * FROM environment_generations WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        evidence = _read(row["evidence"])
        spec = evidence["spec"]
        interpreter = (
            Path(spec["interpreter"])
            if spec["kind"] == "existing"
            else self.cache / identity / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        return EnvironmentGeneration(identity, interpreter, _mapping(freeze(evidence)), row["state"])

    @property
    def selected(self):
        with self.store._mutex:
            assert self.store._db is not None
            row = self.store._db.execute("SELECT id FROM environment_generations WHERE state='selected'").fetchone()
        return None if row is None else self.get(row[0])

    def _verify(self, generation):
        spec = generation.evidence["spec"]
        actual = (
            self.installation_probe(generation) if spec.get("runtime_format") else self._probe(generation.interpreter)
        )
        if generation.evidence["spec"]["kind"] == "managed":
            directory = self.cache / generation.id
            actual["distribution_roots"] = [str(Path(p).relative_to(directory)) for p in actual["distribution_roots"]]
        if _probe_identity(spec, actual) != _probe_identity(spec, _plain(generation.evidence)["probe"]):
            raise ValueError("Environment changed after preparation; prepare a new generation")

    def owners(self, identity):
        with self.store._mutex:
            assert self.store._db is not None
            return tuple(
                row[0]
                for row in self.store._db.execute(
                    "SELECT owner_id FROM \"references\" WHERE owner_kind='environment-owner' "
                    "AND target_kind='environment' AND target_id=? ORDER BY owner_id",
                    (identity,),
                )
            )

    def acquire(self, generation_id, owner_id):
        with self._mutex:
            generation = self.get(generation_id)
            self._verify(generation)
            with self.store._transaction() as db:
                old = db.execute(
                    "SELECT target_id FROM \"references\" WHERE owner_kind='environment-owner' AND owner_id=?",
                    (owner_id,),
                ).fetchone()
                if old is not None and old[0] != generation_id:
                    raise ValueError("Owner already pins a different generation")
                db.execute(
                    "INSERT OR IGNORE INTO \"references\" VALUES ('environment-owner',?,'environment',?)",
                    (owner_id, generation_id),
                )
            return generation

    def release(self, owner_id):
        with self.store._transaction() as db:
            db.execute("DELETE FROM \"references\" WHERE owner_kind='environment-owner' AND owner_id=?", (owner_id,))

    def select(self, generation_id):
        with self._mutex:
            generation = self.get(generation_id)
            self._verify(generation)
            current = self.selected
            if current is not None and current.id == generation_id:
                return "selected"
            with self.store._mutex:
                assert self.store._db is not None
                busy = self.store._db.execute(
                    "SELECT 1 FROM \"references\" WHERE owner_kind='environment-owner' LIMIT 1"
                ).fetchone()
            if busy:
                return "busy"
            return "restart_required"

    def activate(self, generation_id):
        """Called only after launcher handoff capability validation, with no active owners."""
        with self._mutex:
            if self.select(generation_id) == "busy":
                raise ValueError("Environment activation is busy")
            with self.store._transaction() as db:
                db.execute("UPDATE environment_generations SET state='retired' WHERE state='selected'")
                db.execute("UPDATE environment_generations SET state='selected' WHERE id=?", (generation_id,))

    def use_existing(self, interpreter):
        interpreter = Path(interpreter).absolute()
        probe = self._probe(interpreter)
        spec = {"kind": "existing", "interpreter": str(interpreter), "runtime": probe["runtime"]}
        identity = environment_identity(spec)
        self._save(identity, {"spec": spec, "probe": probe})
        return self.get(identity)

    def cleanup(self, generation_id):
        with self._mutex:
            generation = self.get(generation_id)
            if self.owners(generation_id):
                raise ValueError("Cannot clean up an owned generation")
            if generation.state == "selected":
                raise ValueError("Cannot clean up the selected generation")
            with self.store._transaction() as db:
                if db.execute(
                    'SELECT 1 FROM "references" WHERE target_kind=? AND target_id=?', ("environment", generation_id)
                ).fetchone():
                    raise ValueError("Cannot clean up a referenced generation")
            if generation.evidence["spec"]["kind"] == "existing":
                raise ValueError("Existing environments are managed outside Studio")
            remove_tree(self.cache / generation_id)

    def reconstruct(self, evidence):
        evidence = _plain(evidence)
        spec = evidence["spec"]
        identity = environment_identity(spec)
        try:
            if spec["kind"] == "existing":
                generation = self.get(identity)
                self._verify(generation)
                return generation
            if spec["platform"] != sys.platform or spec["architecture"] != platform.machine():
                raise ValueError("Captured interpreter platform is unavailable")
            with self._mutex:
                return self._prepare_spec(spec, expected_probe=evidence["probe"])
        except (ValueError, OSError, KeyError) as error:
            raise ValueError(f"Historical environment reconstruction unavailable: {error}") from error
