"""Supervisor side of remote staging and uv provisioning (design section 9, D47, D101, D105).

The remote cache is ``<cache>/{runtime/<hash>, blobs/<sha256>, envs/<spec-hash>, attempts/<attempt>}``.
Blobs are content-addressed and published only after their SHA-256 verifies. Environments hold
external dependencies only, are built by ``uv sync`` under an exclusive lock, and are read-only
once their ``published`` marker exists. Attempt directories hold one read-only snapshot each and
are removed when the attempt ends. No model code is imported here.
"""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
import fnmatch
import hashlib
import os
import posixpath
from pathlib import Path
import secrets
import shutil
import signal
import stat
import subprocess
import tarfile
import threading
import time
import tomllib
from typing import Any

from simulatte._wire import canonical_pack, freeze
from simulatte.execution.durability import replace_durable, sync_directory
from simulatte.execution.protocol import MAX_FRAGMENT
from simulatte.execution.runtime import describe_runtime, provisioning_spec_hash
from simulatte.execution.schema import _hash, _plain, _runtime
from simulatte.execution.snapshot import _path as portable_path

MAX_BLOB = 1 << 30
STAGE_KINDS = ("source", "inputs", "document")
_STDERR_TAIL = 4096
_LOCK_POLL = 0.1


class RemoteError(ValueError):
    """A staging or provisioning step failed for a known reason; the worker answers ``error``."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.details = code, details

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)[:4096], **self.details}


def remove_tree(path: Path) -> None:
    """Remove a possibly read-only tree without following (or changing) symlinks."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if not path.exists():
        return
    path.chmod(0o700)
    for directory, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            item = Path(directory) / name
            if not item.is_symlink():
                item.chmod(0o700 if item.is_dir() else 0o600)
    shutil.rmtree(path)


def _read_only(path: Path, *, include_root: bool = True) -> None:
    """Drop write permission below ``path`` (and on it); symlinks are never followed."""
    for directory, directories, files in os.walk(path, topdown=False, followlinks=False):
        for name in files + directories:
            item = Path(directory) / name
            if not item.is_symlink():
                item.chmod(stat.S_IMODE(item.lstat().st_mode) & ~0o222)
    if include_root:
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)


def extract_archive(archive: Path, destination: Path, *, only: frozenset[str] | None = None) -> set[str]:
    """Extract a hash-verified snapshot archive: portable regular-file members, each once.

    ``only`` restricts extraction to these member names. Returns the extracted names. A
    malformed archive is a ``ValueError``, never an unhandled ``tarfile`` error.
    """
    try:
        return _extract(archive, destination, only)
    except tarfile.TarError as error:
        raise ValueError(f"Invalid staged archive: {error}") from error


def archive_names(archive: Path) -> set[str]:
    try:
        with tarfile.open(archive, "r:") as members:
            return set(members.getnames())
    except tarfile.TarError as error:
        raise ValueError(f"Invalid staged archive: {error}") from error


def _extract(archive: Path, destination: Path, only: frozenset[str] | None) -> set[str]:
    seen: set[str] = set()
    folded: set[str] = set()
    with tarfile.open(archive, "r:") as members:
        for member in members:
            name = portable_path(member.name)
            if not member.isreg() or name in seen or name.casefold() in folded:
                raise ValueError("Invalid staged archive member")
            seen.add(name)
            folded.add(name.casefold())
            if only is not None and name not in only:
                continue
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            content = members.extractfile(member)
            assert content is not None  # Regular members always have a payload.
            with content, target.open("xb") as sink:
                shutil.copyfileobj(content, sink, MAX_FRAGMENT)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return seen if only is None else seen & only


class BlobUpload:
    """One incoming blob: fsynced fragments in order, published only when the hash verifies."""

    def __init__(self, store: BlobStore, kind: str, sha256: str, length: int):
        self.store, self.kind, self.sha256, self.length = store, kind, sha256, length
        self.received = 0
        self._digest = hashlib.sha256()
        # A per-session name: two slots on one host may stage the same blob at once.
        self.part = store.root / f"{sha256}.{secrets.token_hex(8)}.part"
        self.part.touch(exist_ok=False)

    def write(self, offset: int, payload: bytes) -> bool:
        """Append the next fragment durably; ``True`` once the complete blob is published."""
        if offset != self.received or not 0 < len(payload) <= MAX_FRAGMENT or offset + len(payload) > self.length:
            raise ValueError("Staging fragment out of order or out of bounds")
        with self.part.open("r+b") as file:
            file.seek(offset)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        self._digest.update(payload)
        self.received += len(payload)
        if self.received < self.length:
            return False
        if self._digest.hexdigest() != self.sha256:
            self.discard()
            raise RemoteError("stage_mismatch", "Staged blob content does not match its SHA-256", sha256=self.sha256)
        self.part.chmod(0o444)
        replace_durable(self.part, self.store.path(self.sha256))
        return True

    def discard(self) -> None:
        self.part.unlink(missing_ok=True)


class BlobStore:
    """``<cache>/blobs/<sha256>``: verified, read-only, content-addressed staged bytes."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path(self, sha256: str) -> Path:
        _hash(sha256)
        return self.root / sha256

    def begin(self, body: Mapping[str, Any]) -> BlobUpload | None:
        """``None`` when the blob is already held (``staged {cached: true}``, zero fragments)."""
        kind, sha256, length = body.get("kind"), body.get("sha256"), body.get("length")
        if kind not in STAGE_KINDS or type(length) is not int or not 0 < length <= MAX_BLOB:
            raise ValueError("Invalid stage request")
        path = self.path(str(sha256))
        if path.is_file() and path.stat().st_size == length:
            return None
        if path.exists() or path.is_symlink():
            remove_tree(path)  # A truncated or foreign entry is never trusted; stage it again.
        return BlobUpload(self, kind, str(sha256), length)

    def require(self, sha256: str) -> Path:
        path = self.path(sha256)
        if not path.is_file() or path.is_symlink():
            raise RemoteError("staging_failed", f"Blob {sha256} is not staged on this worker")
        return path


def _document(value: Mapping[str, Any]) -> bytes:
    """The D84 document envelope ``read_document`` validates (``schema``, ``kind``, ``value``)."""
    return canonical_pack(freeze({"schema": 1, "kind": "runtime", "value": value}))


def _site_packages(environment: Path) -> Path:
    found = sorted(environment.glob("lib/*/site-packages"))
    if len(found) != 1:
        raise RemoteError("provisioning_failed", "Provisioned environment has no single site-packages", reason="layout")
    return found[0]


def _local_packages(spec, lock: Path, members: set[str]) -> tuple[set[str], set[str]]:
    """Archive names of each local package's ``pyproject.toml`` (or ``path`` archive file)."""
    from simulatte.execution.preflight import LOCAL_SOURCES, normalize

    try:
        data = tomllib.loads(lock.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise RemoteError("provisioning_failed", f"Staged uv.lock is malformed: {error}", reason="project") from None
    local = set(spec["local_packages"])
    projects, files = set(), set()
    for package in data.get("package", []):
        source = package.get("source", {})
        if normalize(str(package.get("name", ""))) not in local or not isinstance(source, Mapping):
            continue
        for kind in LOCAL_SOURCES:
            if kind not in source:
                continue
            root = posixpath.normpath(str(source[kind]))
            name = "pyproject.toml" if root == "." else f"{root}/pyproject.toml"
            if name in members:
                projects.add(name)
            elif kind == "path" and root in members:
                files.add(root)
    return projects, files


def _referenced(build: Path, name: str, members: set[str]) -> set[str]:
    """Readme and license files the local ``pyproject.toml`` ``name`` refers to, when in the snapshot."""
    try:
        project = tomllib.loads((build / name).read_text(encoding="utf-8")).get("project", {})
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise RemoteError("provisioning_failed", f"Malformed {name}: {error}", reason="project") from None
    base = posixpath.dirname(name)
    files = []
    for field in ("readme", "license"):
        value = project.get(field)
        if isinstance(value, str) and field == "readme":
            files.append(value)
        elif isinstance(value, Mapping) and isinstance(value.get("file"), str):
            files.append(value["file"])
    found = {posixpath.normpath(posixpath.join(base, item)) for item in files} & members
    for pattern in project.get("license-files", []):
        if isinstance(pattern, str):
            found |= {item for item in members if fnmatch.fnmatchcase(item, posixpath.join(base, pattern))}
    return found


class Provisioner:
    """Build and publish ``<cache>/envs/<spec-hash>`` with ``uv sync``; never modify a published one."""

    def __init__(self, cache: Path, *, uv: str | None = None):
        self.cache = Path(cache)
        self.envs = self.cache / "envs"
        self.uv = uv
        self._process: subprocess.Popen | None = None
        self._aborted = threading.Event()

    def _uv(self) -> str:
        candidates = [self.uv, os.environ.get("UV"), shutil.which("uv")]
        home = Path.home()
        candidates += [str(home / ".local" / "bin" / "uv"), str(home / ".cargo" / "bin" / "uv")]
        for candidate in candidates:
            if candidate and os.access(candidate, os.X_OK) and Path(candidate).is_file():
                return candidate
        raise RemoteError("uv_missing", "uv is missing on this worker")

    def environment(self, spec_hash: str) -> dict[str, Any]:
        """The published environment's answer; ``RemoteError`` when it is not published."""
        _hash(spec_hash)
        path = self.envs / spec_hash
        evidence = path / "evidence.msgpack"
        if not (path / "published").is_file() or not evidence.is_file():
            raise RemoteError("provisioning_failed", "Environment is not provisioned", reason="unpublished")
        data = evidence.read_bytes()
        from simulatte._wire import unpack

        facts = _plain(unpack(data))["value"]["facts"]
        return {
            "spec_hash": spec_hash,
            "evidence_sha256": hashlib.sha256(data).hexdigest(),
            "evidence": {"path": str(evidence), "length": len(data), "sha256": hashlib.sha256(data).hexdigest()},
            "python": facts["python"],
            "site_packages": str(_site_packages(path)),
        }

    def abort(self) -> None:
        """Stop a running build (supervisor shutdown); the build then fails and publishes nothing."""
        self._aborted.set()
        process = self._process
        if process is not None and process.poll() is None:
            with contextlib.suppress(OSError):  # It may have exited since the poll.
                os.killpg(process.pid, signal.SIGKILL)

    def provision(self, spec: Mapping[str, Any], source_blob: Path, *, timeout: float) -> dict[str, Any]:
        spec = _plain(_runtime(spec))
        if spec.get("kind") != "uv":
            raise ValueError("Provisioning needs a uv runtime spec")
        if type(timeout) not in (int, float) or not 0 < timeout < float("inf"):
            raise ValueError("Provisioning timeout must be positive and finite")
        deadline = time.monotonic() + timeout
        spec_hash = provisioning_spec_hash(spec)
        self.envs.mkdir(parents=True, exist_ok=True)
        environment = self.envs / spec_hash
        if (environment / "published").is_file():
            return self._answer(spec_hash)
        import fcntl

        with (self.envs / f"{spec_hash}.lock").open("a+b") as lock:
            while True:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RemoteError(
                            "provisioning_failed", "Timed out waiting for another build", reason="timeout"
                        ) from None
                    time.sleep(_LOCK_POLL)
            try:
                if (environment / "published").is_file():
                    return self._answer(spec_hash)
                build = self.envs / f"{spec_hash}.build"
                remove_tree(build)
                remove_tree(environment)  # An unpublished environment is a failed or interrupted build.
                try:
                    build.mkdir()
                    self._project(spec, source_blob, build)
                    self._sync(spec, build, environment, deadline)
                    self._publish(environment)
                except BaseException:
                    remove_tree(environment)
                    raise
                finally:
                    remove_tree(build)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return self._answer(spec_hash)

    def _answer(self, spec_hash: str) -> dict[str, Any]:
        found = self.environment(spec_hash)
        return {name: found[name] for name in ("spec_hash", "evidence_sha256", "python", "site_packages")}

    @staticmethod
    def _project(spec, source_blob: Path, build: Path) -> None:
        """Materialize what ``uv sync --locked`` reads: the project files and local package metadata.

        The root ``pyproject.toml``, ``uv.lock`` and ``.python-version``, then each local package's
        ``pyproject.toml`` (uv validates the lock against it) and the readme and license files it
        references. Local package sources are never materialized here: they are not installed.
        """
        names = extract_archive(source_blob, build, only=frozenset(("pyproject.toml", "uv.lock", ".python-version")))
        if not {"pyproject.toml", "uv.lock"} <= names:
            raise RemoteError("provisioning_failed", "Staged source lacks pyproject.toml or uv.lock", reason="project")
        for name, field in (("pyproject.toml", "pyproject_hash"), ("uv.lock", "lock_hash")):
            with (build / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != spec[field]:
                    raise RemoteError("provisioning_failed", f"Staged {name} differs from the spec", reason="project")
        members = archive_names(source_blob)
        projects, files = _local_packages(spec, build / "uv.lock", members)
        extract_archive(source_blob, build, only=frozenset(projects - names))
        for project in projects:
            files |= _referenced(build, project, members)
        extract_archive(source_blob, build, only=frozenset(files - names - projects))

    @staticmethod
    def argv(uv: str, spec: Mapping[str, Any]) -> list[str]:
        """The exact ``uv sync`` command of design section 9."""
        command = [uv, "sync", "--locked", "--no-install-project", "--no-install-workspace"]
        command += ["--no-install-package", "simulatte"]
        for name in spec["local_packages"]:
            if name != "simulatte":
                command += ["--no-install-package", name]
        for name in spec["extras"]:
            command += ["--extra", name]
        for name in spec["groups"]:
            command += ["--group", name]
        return [*command, "--python", spec["python"]]

    def _sync(self, spec, build: Path, environment: Path, deadline: float) -> None:
        command = self.argv(self._uv(), spec)
        # The host's own uv configuration and credentials apply; only the target is set here.
        env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)}
        tail = bytearray()
        try:
            process = subprocess.Popen(
                command,
                cwd=build,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as error:
            raise RemoteError("uv_missing", f"Cannot run uv: {error}") from error
        self._process = process
        assert process.stderr is not None

        def drain():
            assert process.stderr is not None
            while data := process.stderr.read(65536):
                tail.extend(data)
                del tail[:-_STDERR_TAIL]

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        timed_out = False
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline or self._aborted.is_set():
                    timed_out = not self._aborted.is_set()
                    with contextlib.suppress(OSError):  # ``abort`` may have killed the group already.
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                time.sleep(0.05)
        finally:
            self._process = None
            try:
                # uv's whole session ends with it: leftovers would hold the stderr pipe open.
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            reader.join(5)
            process.stderr.close()
        stderr = bytes(tail[-_STDERR_TAIL:]).decode("utf-8", errors="replace")
        if timed_out:
            raise RemoteError("provisioning_failed", "uv sync timed out", reason="timeout", stderr=stderr)
        if self._aborted.is_set():
            raise RemoteError("provisioning_failed", "Provisioning was aborted", reason="aborted", stderr=stderr)
        if process.returncode != 0:
            raise RemoteError(
                "provisioning_failed", f"uv sync exited with {process.returncode}", reason="uv_failed", stderr=stderr
            )

    @staticmethod
    def _publish(environment: Path) -> None:
        site = _site_packages(environment)
        data = _document(describe_runtime([str(site)]))
        evidence = environment / "evidence.msgpack"
        with evidence.open("xb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        _read_only(environment, include_root=False)
        with (environment / "published").open("xb") as marker:
            os.fsync(marker.fileno())
        (environment / "published").chmod(0o444)
        sync_directory(environment)
        environment.chmod(stat.S_IMODE(environment.stat().st_mode) & ~0o222)


class RemoteCache:
    """The worker's cache root: blobs, environments and per-attempt snapshots."""

    def __init__(self, root: Path | str, *, runtime_root: Path, trusted_runtime_hash: str | None, uv: str | None):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()  # Document references must be symlink-free paths.
        self.blobs = BlobStore(self.root / "blobs")
        self.provisioner = Provisioner(self.root, uv=uv)
        self.runtime_root = runtime_root
        self.trusted_runtime_hash = trusted_runtime_hash

    def _blob_reference(self, reference: Any) -> dict[str, Any]:
        if not isinstance(reference, Mapping) or set(reference) != {"sha256", "length"}:
            raise ValueError("Invalid staged document reference")
        return {"path": str(self.blobs.require(reference["sha256"])), **_plain(reference)}

    def attempt(self, attempt_id: str, body: Mapping[str, Any]) -> tuple[dict[str, Any], Path]:
        """Materialize the staged snapshot read-only and return the run body with host-local paths.

        The coordinator names blobs and the environment by hash; only the supervisor knows its
        paths. ``external_roots`` are the frozen runtime first, then the environment's
        site-packages; the supervisor-only dependency root is never among them.
        """
        body = _plain(body)
        config = body["config"]
        staged = config.pop("staged_snapshot")
        environment = config.pop("environment")
        if set(staged) != {"source_sha256", "inputs_sha256", "python_path"} or set(environment) != {
            "spec_hash",
            "evidence_sha256",
        }:
            raise ValueError("Invalid staged run configuration")
        provisioned = self.provisioner.environment(environment["spec_hash"])
        if provisioned["evidence_sha256"] != environment["evidence_sha256"]:
            raise ValueError("Provisioned environment evidence differs")
        directory = self.root / "attempts" / attempt_id
        directory.mkdir(parents=True, exist_ok=False)
        try:
            # Referenced hashes keep ``workers prune`` away from blobs and environments in use.
            names = [staged["source_sha256"], staged["inputs_sha256"], environment["spec_hash"]]
            for key, value in (*config.items(), *body.items()):
                if key.endswith("_ref") and isinstance(value, Mapping):
                    names.append(str(value.get("sha256")))
            (directory / "refs").write_text("".join(f"{name}\n" for name in names), encoding="utf-8")
            snapshot = directory / "snapshot"
            snapshot.mkdir()
            extract_archive(self.blobs.require(staged["source_sha256"]), snapshot)
            extract_archive(self.blobs.require(staged["inputs_sha256"]), snapshot)
            _read_only(snapshot)
            roots = [snapshot if name == "." else snapshot / portable_path(name) for name in staged["python_path"]]
            site = provisioned["site_packages"]
            config.update(
                project_root=str(snapshot),
                snapshot_root=str(snapshot),
                snapshot_roots=list(map(str, roots)),
                external_roots=[str(self.runtime_root), site],
                distribution_roots=[site],
                runtime_ref=provisioned["evidence"],
                trusted_runtime_hash=self.trusted_runtime_hash,
            )
            for key in [key for key in config if key.endswith("_ref") and key != "runtime_ref"]:
                config[key] = self._blob_reference(config[key])
            for key in [key for key in body if key.endswith("_ref")]:
                body[key] = self._blob_reference(body[key])
        except BaseException:
            remove_tree(directory)
            raise
        return body, directory
