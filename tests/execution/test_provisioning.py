"""uv provisioning on the worker (``Provisioner``) and remote-kind replications end to end.

``fake_uv.py`` stands in for uv. End-to-end tests run the remote side for real on this machine:
the fake ``ssh`` ``local-sh`` destination runs the probe, stage and bootstrap scripts with
``/bin/sh``, the real supervisor stages and provisions into ``$HOME/cache``, and the fake uv's
environment links ``site-packages`` to this interpreter's ``purelib``.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tarfile
import time

import pytest

from simulatte.execution import coordinator as m, staging
from simulatte.execution.provisioning import (
    Provisioner,
    RemoteCache,
    RemoteError,
    archive_names,
    extract_archive,
    remove_tree,
)
from simulatte.execution.references import write_document
from simulatte.execution.runtime import RuntimeChanged, provisioning_spec_hash, verify_runtime
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.execution.store import _read
from simulatte.experiments import load_experiment
from simulatte.provenance import _simulatte_version
from tests.execution.test_coordinator import MODEL
from tests.execution.test_preflight import PYPI, lock_text
from tests.execution.test_scheduler import write_experiment
from tests.execution.test_workers_cli import fake_ssh  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows is never a remote host")

FAKE_UV = Path(__file__).with_name("fake_uv.py")
PYTHON = "3.14.1"
HASH = "c" * 64
PYPROJECT = b'[project]\nname = "plant"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n'
LOCK = lock_text(("simulatte", _simulatte_version(), PYPI), ("plant", "0.1.0", '{ virtual = "." }')).encode()
SHOP_LOCK = lock_text(
    ("simulatte", _simulatte_version(), PYPI),
    ("plant", "0.1.0", '{ virtual = "." }'),
    ("shopkit", "0.1.0", '{ directory = "packages/shopkit" }'),
).encode()


def install_uv(directory: Path, **mode) -> Path:
    path = directory / "uv"
    path.write_text(f"#!{sys.executable}\n{FAKE_UV.read_text(encoding='utf-8')}", encoding="utf-8")
    path.chmod(0o755)
    (directory / "fake-uv.json").write_text(json.dumps(mode))
    return path


def uv_calls(directory: Path):
    path = directory / "uv.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def archive(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as target:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            target.addfile(info, io.BytesIO(data))
    return path


def spec(**changes):
    value = {
        "kind": "uv",
        "python": PYTHON,
        "lock_hash": hashlib.sha256(LOCK).hexdigest(),
        "pyproject_hash": hashlib.sha256(PYPROJECT).hexdigest(),
        "extras": [],
        "groups": [],
        "local_packages": ["plant"],
        "trusted_runtime_hash": HASH,
    }
    return value | changes


@pytest.fixture(autouse=True)
def writable(tmp_path):
    """Published environments and snapshots are read-only; make them removable for pytest."""
    yield
    for cache in (tmp_path / "cache", tmp_path / "home" / "cache"):
        if cache.exists():
            remove_tree(cache)


@pytest.fixture
def source(tmp_path):
    return archive(tmp_path / "source.tar", {"pyproject.toml": PYPROJECT, "uv.lock": LOCK, "plant.py": b"x = 1\n"})


@pytest.fixture
def tools(tmp_path):
    path = tmp_path / "tools"
    path.mkdir()
    return path


# ----- Provisioner -----


def test_sync_runs_the_exact_command_and_publishes_a_read_only_environment(tmp_path, source, tools, monkeypatch):
    uv = install_uv(tools, site="dir")
    monkeypatch.setenv("UV_INDEX_URL", "https://host-own.example/simple")  # The host's own uv config applies.
    provisioner = Provisioner(tmp_path / "cache", uv=str(uv))
    wanted = spec(local_packages=["plant", "simulatte", "tools"], extras=["fast"], groups=["dev", "docs"])
    answer = provisioner.provision(wanted, source, timeout=30)
    identity = provisioning_spec_hash(wanted)
    environment = tmp_path / "cache" / "envs" / identity
    (call,) = uv_calls(tools)
    assert call["argv"] == [
        "sync",
        "--locked",
        "--no-install-project",
        "--no-install-workspace",
        "--no-install-package",
        "simulatte",
        "--no-install-package",
        "plant",
        "--no-install-package",
        "tools",
        "--extra",
        "fast",
        "--group",
        "dev",
        "--group",
        "docs",
        "--python",
        PYTHON,
    ]
    assert call["files"] == ["pyproject.toml", "uv.lock"] and call["environment"] == str(environment)
    assert call["env"]["UV_PROJECT_ENVIRONMENT"] == str(environment) and "UV_NO_CONFIG" not in call["env"]
    assert call["env"]["UV_INDEX_URL"] == "https://host-own.example/simple"
    evidence = environment / "evidence.msgpack"
    assert answer == {
        "spec_hash": identity,
        "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "python": answer["python"],
        "site_packages": str(next(environment.glob("lib/*/site-packages"))),
    }
    assert (environment / "published").is_file()
    assert not any(path.stat().st_mode & 0o222 for path in [environment, *environment.rglob("*")])
    assert sorted(path.name for path in environment.parent.iterdir()) == [identity, f"{identity}.lock"]
    # The same spec (any trusted runtime) is published already: uv does not run again.
    assert provisioner.provision(wanted | {"trusted_runtime_hash": "d" * 64}, source, timeout=30) == answer
    assert len(uv_calls(tools)) == 1


def test_missing_uv_is_uv_missing(tmp_path, source, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("UV", raising=False)
    with pytest.raises(RemoteError) as caught:
        Provisioner(tmp_path / "cache").provision(spec(), source, timeout=30)
    assert caught.value.code == "uv_missing"
    with pytest.raises(RemoteError) as caught:
        Provisioner(tmp_path / "cache", uv=str(source)).provision(spec(), source, timeout=30)  # Not executable.
    assert caught.value.code == "uv_missing"


def test_a_failing_sync_publishes_nothing_and_keeps_a_bounded_stderr_tail(tmp_path, source, tools):
    uv = install_uv(tools, exit=2, stderr="x" * 10000 + "resolution failed")
    with pytest.raises(RemoteError) as caught:
        Provisioner(tmp_path / "cache", uv=str(uv)).provision(spec(), source, timeout=30)
    error = caught.value
    assert error.code == "provisioning_failed" and error.details["reason"] == "uv_failed"
    assert len(error.details["stderr"]) <= 4096 and error.details["stderr"].endswith("resolution failed")
    assert [path.name for path in (tmp_path / "cache" / "envs").iterdir()] == [provisioning_spec_hash(spec()) + ".lock"]
    assert error.body()["code"] == "provisioning_failed"


def test_timeout_kills_the_uv_process_group(tmp_path, source, tools):
    uv = install_uv(tools, sleep=30)
    began = time.monotonic()
    with pytest.raises(RemoteError) as caught:
        Provisioner(tmp_path / "cache", uv=str(uv)).provision(spec(), source, timeout=1)
    assert caught.value.code == "provisioning_failed" and caught.value.details["reason"] == "timeout"
    assert time.monotonic() - began < 10
    sleeper = int((tools / "sleeper.pid").read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _alive(sleeper):
        time.sleep(0.05)
    assert not _alive(sleeper)
    assert not any(path.name.endswith(".build") or path.is_dir() for path in (tmp_path / "cache" / "envs").iterdir())


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A reparented zombie is gone for every practical purpose.
    status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(status) and not status.startswith("Z")


def test_source_that_differs_from_the_spec_is_refused(tmp_path, tools):
    uv = install_uv(tools)
    other = archive(tmp_path / "other.tar", {"pyproject.toml": PYPROJECT, "uv.lock": b"version = 1\n"})
    missing = archive(tmp_path / "missing.tar", {"pyproject.toml": PYPROJECT})
    for blob in (other, missing):
        with pytest.raises(RemoteError) as caught:
            Provisioner(tmp_path / "cache", uv=str(uv)).provision(spec(), blob, timeout=30)
        assert caught.value.details["reason"] == "project"
    for value, timeout in ((spec(kind="local"), 1), (spec(), 0)):
        with pytest.raises(ValueError):
            Provisioner(tmp_path / "cache", uv=str(uv)).provision(value, other, timeout=timeout)
    assert uv_calls(tools) == []


def test_unsafe_archive_members_are_refused(tmp_path):
    with tarfile.open(tmp_path / "bad.tar", "w") as target:
        info = tarfile.TarInfo("../escape")
        target.addfile(info, io.BytesIO(b""))
    with pytest.raises(ValueError):
        extract_archive(tmp_path / "bad.tar", tmp_path / "out")
    with tarfile.open(tmp_path / "dup.tar", "w") as target:
        for _ in range(2):
            target.addfile(tarfile.TarInfo("a.txt"), io.BytesIO(b""))
    with pytest.raises(ValueError, match="Invalid staged archive member"):
        extract_archive(tmp_path / "dup.tar", tmp_path / "out")


def test_mutated_environment_fails_child_verification(tmp_path, source, tools):
    """The run configuration the supervisor builds verifies until the environment changes."""
    uv = install_uv(tools, site="dir")
    cache = RemoteCache(tmp_path / "cache", runtime_root=tmp_path / "runtime", trusted_runtime_hash=HASH, uv=str(uv))
    wanted = spec(python=".".join(__import__("platform").python_version().split(".")[:2]))
    answer = cache.provisioner.provision(wanted, source, timeout=30)
    blobs = {}
    for name, path in (("source", source), ("inputs", archive(tmp_path / "inputs.tar", {"input.txt": b"5"}))):
        blobs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (cache.blobs.root / blobs[name]).write_bytes(path.read_bytes())
    layout = write_document(cache.blobs.root, "layout", {"schema_version": "1.0"})
    staged_layout = cache.blobs.root / layout["sha256"]
    Path(layout["path"]).rename(staged_layout)
    body = {
        "config": {
            "reference": "plant.py",
            "staged_snapshot": {
                "source_sha256": blobs["source"],
                "inputs_sha256": blobs["inputs"],
                "python_path": ["."],
            },
            "environment": {"spec_hash": answer["spec_hash"], "evidence_sha256": answer["evidence_sha256"]},
            "layout_ref": {"sha256": layout["sha256"], "length": layout["length"]},
        },
        "timeout": None,
    }
    localized, directory = cache.attempt("f" * 32, body)
    config = localized["config"]
    snapshot = directory / "snapshot"
    assert config["project_root"] == config["snapshot_root"] == str(snapshot)
    assert config["snapshot_roots"] == [str(snapshot)]
    assert config["external_roots"] == [str(tmp_path / "runtime"), answer["site_packages"]]
    assert config["distribution_roots"] == [answer["site_packages"]] and config["trusted_runtime_hash"] == HASH
    assert config["layout_ref"]["path"] == str(staged_layout)
    assert sorted(path.name for path in snapshot.iterdir()) == ["input.txt", "plant.py", "pyproject.toml", "uv.lock"]
    assert not any(path.stat().st_mode & 0o222 for path in snapshot.rglob("*"))
    assert set((directory / "refs").read_text().split()) == {*blobs.values(), answer["spec_hash"], layout["sha256"]}
    resolved, _, _ = verify_runtime(config, wanted)
    assert resolved["kind"] == "uv" and resolved["spec_hash"] == answer["spec_hash"]
    assert resolved["trusted_runtime_hash"] == HASH and resolved["facts"]["python"] == answer["python"]
    for requested in (spec(python="2.7"), spec(python=wanted["python"], trusted_runtime_hash="d" * 64)):
        with pytest.raises(RuntimeChanged, match="Installed runtime changed after preparation"):
            verify_runtime(config, requested)
    site = Path(answer["site_packages"])
    with pytest.raises(PermissionError):
        (site / "intruder.txt").write_text("x")
    site.chmod(0o755)
    added = site / "intruder-2.0.dist-info"
    added.mkdir()
    (added / "METADATA").write_text("Metadata-Version: 2.1\nName: intruder\nVersion: 2.0\n")
    with pytest.raises(RuntimeChanged, match="Installed runtime changed after preparation"):
        verify_runtime(config, wanted)
    with pytest.raises(FileExistsError):
        cache.attempt("f" * 32, body)


def test_attempt_configuration_must_name_staged_blobs_and_the_provisioned_environment(tmp_path, source, tools):
    uv = install_uv(tools, site="dir")
    cache = RemoteCache(tmp_path / "cache", runtime_root=tmp_path, trusted_runtime_hash=HASH, uv=str(uv))
    answer = cache.provisioner.provision(spec(), source, timeout=30)
    environment = {"spec_hash": answer["spec_hash"], "evidence_sha256": answer["evidence_sha256"]}
    staged = {"source_sha256": "1" * 64, "inputs_sha256": "1" * 64, "python_path": ["."]}
    blob = hashlib.sha256(source.read_bytes()).hexdigest()
    (cache.blobs.root / blob).write_bytes(source.read_bytes())
    empty = archive(tmp_path / "empty.tar", {}).read_bytes()
    (cache.blobs.root / hashlib.sha256(empty).hexdigest()).write_bytes(empty)
    for config, error in (
        ({"staged_snapshot": staged, "environment": {"spec_hash": "2" * 64, "evidence_sha256": "3" * 64}}, RemoteError),
        ({"staged_snapshot": staged, "environment": {**environment, "evidence_sha256": "3" * 64}}, ValueError),
        ({"staged_snapshot": {}, "environment": environment}, ValueError),
        ({"staged_snapshot": staged, "environment": environment}, RemoteError),
        (
            {
                "staged_snapshot": {
                    **staged,
                    "source_sha256": blob,
                    "inputs_sha256": hashlib.sha256(empty).hexdigest(),
                },
                "environment": environment,
                "layout_ref": {"path": "/elsewhere"},
            },
            ValueError,
        ),
    ):
        with pytest.raises(error):
            cache.attempt("e" * 32, {"config": config})
    assert not (tmp_path / "cache" / "attempts" / ("e" * 32)).exists()


# ----- end to end through the fake ssh -----


def attempt_outcome(store, attempt_id):
    with store._transaction() as db:
        row = db.execute("SELECT status,details FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    return row["status"], _read(row["details"])


@pytest.fixture
def remote(fake_ssh, tmp_path, monkeypatch):  # noqa: F811
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    install_uv(fake_ssh)
    workers = tmp_path / "workers.toml"

    def configure(timeout=600, **mode):
        (fake_ssh / "fake-uv.json").write_text(json.dumps(mode))
        workers.write_text(
            f'[workers.fake]\nhost = "local-sh"\nslots = 1\ncache = "~/cache"\nprovision_timeout = {timeout}\n'
        )

    configure()
    monkeypatch.setenv("SIMULATTE_WORKERS", str(workers))
    root = tmp_path / "project"
    (root / "packages" / "shopkit" / "src" / "shopkit").mkdir(parents=True)
    (root / "packages" / "shopkit" / "pyproject.toml").write_text('[project]\nname = "shopkit"\nversion = "0.1.0"\n')
    (root / "packages" / "shopkit" / "src" / "shopkit" / "__init__.py").write_text("OFFSET = 0\n")
    (root / "pyproject.toml").write_bytes(
        PYPROJECT + b'[tool.uv.sources]\nshopkit = { path = "packages/shopkit" }\n'
        b'[tool.simulatte.bundle]\ninclude = ["plant.py", "uv.lock", "packages/**"]\n'
    )
    (root / "uv.lock").write_bytes(SHOP_LOCK)
    (root / "plant.py").write_text(
        "import shopkit  # A local path dependency, loaded from the snapshot (D47).\n" + MODEL
    )
    (root / "input.txt").write_text("5")
    return discover_project(root / "plant.py", root), home / "cache", fake_ssh, configure


def test_remote_kind_replications_publish_through_the_unchanged_path(remote, monkeypatch):
    project, cache, tools, _ = remote
    staged = []
    original = m.stage_blob

    def recording(transport, kind, path, sha256, key, **kwargs):
        staged.append((key.attempt_id, kind, original(transport, kind, path, sha256, key, **kwargs)))
        return staged[-1][2]

    monkeypatch.setattr(m, "stage_blob", recording)
    path = write_experiment(project.root, replications={"count": 2}, configurations={"grid": {"n": [3]}})
    with m.Coordinator(project, slots=SlotConfig(local=0)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path), workers=["fake"])
        report = coordinator.wait_experiment(run_id, timeout=50)
        assert report["status"] == "completed" and report["counts"]["succeeded"] == 2
        runtime = coordinator.store.experiment_run(run_id)["preparation"]["runtimes"]
        # No .python-version: the coordinator's X.Y, so each host's uv picks an available patch (D105).
        assert set(runtime) == {"uv"} and runtime["uv"]["python"] == ".".join(coordinator.python.split(".")[:2])
        assert runtime["uv"]["local_packages"] == ["plant", "shopkit"]
        identity = provisioning_spec_hash(runtime["uv"])
        replications = coordinator.store.replications(run_id)
        for item in replications:
            assert item["worker_kind"] == "uv" and item["dispatches"] == 1
            result = coordinator.store.result(item["attempt"])
            resolved = result.manifest["resolved"]["runtime"]
            assert resolved["kind"] == "uv" and resolved["spec_hash"] == identity
            assert (result.path / "artifacts/answer.txt").read_text() == "8"
            attempt = coordinator.store.attempt(result.key)
            assert attempt["slot"] == "fake-1" and attempt["worker"] == "fake"
        assert coordinator.store.slots()["fake-1"] == {**coordinator.store.slots()["fake-1"], "kind": "remote"}
    (call,) = uv_calls(tools)  # The second replication reuses the published environment.
    assert call["files"] == ["packages/shopkit/pyproject.toml", "pyproject.toml", "uv.lock"]
    assert call["argv"][6:10] == ["--no-install-package", "plant", "--no-install-package", "shopkit"]
    assert call["argv"][:7] == ["sync", "--locked", "--no-install-project", "--no-install-workspace"] + [
        "--no-install-package",
        "simulatte",
        "--no-install-package",
    ]
    assert call["argv"][-2:] == ["--python", runtime["uv"]["python"]]
    assert (cache / "envs" / identity / "published").is_file()
    # The environment's site-packages links to this venv: publishing must not touch the link target.
    assert os.access(sysconfig.get_path("purelib"), os.W_OK)
    assert list((cache / "attempts").iterdir()) == []  # Removed once each attempt ended.
    first, second = (attempt for attempt in dict.fromkeys(item[0] for item in staged))
    assert [(kind, cached) for attempt, kind, cached in staged if attempt == first] == [
        ("source", False),
        ("inputs", False),
        ("document", False),
        ("document", False),
        ("document", False),
    ]
    # The second replication ships only its own request document.
    assert [cached for attempt, _, cached in staged if attempt == second] == [True, True, True, True, False]
    # A fresh coordinator (no remote slots yet) replays a uv result on its original worker (D100).
    with m.Coordinator(project, slots=SlotConfig(local=0)) as coordinator:
        replayed = coordinator.replay(replications[0]["request_id"], timeout=40)
        assert replayed.verification.verified is True, replayed.verification.reason
        assert coordinator.store.attempt(replayed.key)["slot"] == "fake-1"
        with pytest.raises(m.ExecutionFailure) as caught:
            coordinator.replay(replications[0]["request_id"], worker="ghost")
        assert caught.value.status == "no_compatible_worker" and "ghost" in str(caught.value)


def test_provisioning_timeout_fails_the_replication_without_redispatch(remote):
    project, cache, tools, configure = remote
    configure(timeout=2.5, sleep=30)
    path = write_experiment(project.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    with m.Coordinator(project, slots=SlotConfig(local=0)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path), workers=["fake"])
        report = coordinator.wait_experiment(run_id, timeout=40)
        events = coordinator.events()
        assert report["status"] == "failed" and report["counts"]["failed"] == 1
        (item,) = coordinator.store.replications(run_id)
        assert item["status"] == "failed" and item["dispatches"] == 1
        status, details = attempt_outcome(coordinator.store, item["attempt"])
        assert status == "failed"
        assert details["code"] == "provisioning_failed" and details["reason"] == "timeout"
    beats = [event for event in events if event["type"] == "heartbeat" and event["body"].get("phase") == "provisioning"]
    assert len(beats) >= 2
    envs = cache / "envs"
    assert not any(path.is_dir() for path in envs.iterdir())  # No environment and no build directory.


def test_staging_hash_mismatch_fails_the_attempt(remote, monkeypatch):
    project, cache, tools, _ = remote
    original = m.stage_blob

    def tampered(transport, kind, path, sha256, key, **kwargs):
        return original(transport, kind, path, "0" * 64 if kind == "inputs" else sha256, key, **kwargs)

    monkeypatch.setattr(m, "stage_blob", tampered)
    path = write_experiment(project.root, replications={"count": 1}, configurations={"grid": {"n": [3]}})
    with m.Coordinator(project, slots=SlotConfig(local=0)) as coordinator:
        run_id = coordinator.start_experiment(load_experiment(path), workers=["fake"])
        report = coordinator.wait_experiment(run_id, timeout=40)
        assert report["status"] == "failed"
        (item,) = coordinator.store.replications(run_id)
        assert item["dispatches"] == 1
        status, details = attempt_outcome(coordinator.store, item["attempt"])
        assert status == "failed" and details["code"] == "stage_mismatch"
    assert uv_calls(tools) == []
    assert not list((cache / "blobs").glob("*.part"))


def test_staging_window_constant_matches_the_transfer_window():
    from simulatte.execution.transport import FramedChannel

    assert staging.WINDOW == FramedChannel.DATA_BYTES == 16 << 20


# ----- the supervisor's staging, provisioning and staged runs (in process) -----


def test_supervisor_provisions_off_the_command_loop_and_materializes_staged_runs(tmp_path, source, tools):
    from simulatte.execution.schema import AttemptKey
    from tests.execution._worker_harness import WorkerHarness
    from tests.execution.test_staging import reply

    uv = install_uv(tools, sleep=1.5)
    key = AttemptKey("a" * 64, "b" * 32, 1)
    harness = WorkerHarness(trusted_runtime_hash=HASH, cache=str(tmp_path / "cache"), uv=str(uv))
    try:
        assert harness.receive().type == "hello"
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        inputs = archive(tmp_path / "inputs.tar", {"input.txt": b"5"})
        inputs_sha = hashlib.sha256(inputs.read_bytes()).hexdigest()
        request = write_document(tmp_path / "documents", "request", {"not": "a request"})
        for kind, path, sha in (
            ("source", source, source_sha),
            ("inputs", inputs, inputs_sha),
            ("document", Path(request["path"]), request["sha256"]),
        ):
            staging.stage_blob(harness, kind, path, sha, key)
        wanted = spec(python=".".join(__import__("platform").python_version().split(".")[:2]))
        harness.send("provision", {"spec": wanted, "source_sha256": source_sha, "timeout": 30}, key=key)
        harness.send("provision", {"spec": wanted, "source_sha256": source_sha, "timeout": 30}, key=key)
        assert reply(harness).body["code"] == "worker_busy"
        beats = []
        while (message := harness.receive(10)).type == "heartbeat":
            beats.append((message.envelope["attempt_id"], message.body["phase"]))
        assert message.type == "provisioned" and (key.attempt_id, "provisioning") in beats
        answer = dict(message.body)
        harness.send("provision", {"spec": wanted, "source_sha256": "9" * 64, "timeout": 30}, key=key)
        assert reply(harness).body["code"] == "staging_failed"
        garbage = tmp_path / "garbage.tar"
        garbage.write_bytes(b"\x00not a tar" * 10)
        garbage_sha = hashlib.sha256(garbage.read_bytes()).hexdigest()
        staging.stage_blob(harness, "source", garbage, garbage_sha, key)
        other = {**wanted, "extras": ["fresh"]}  # An unpublished spec: the build reads the bad blob.
        harness.send("provision", {"spec": other, "source_sha256": garbage_sha, "timeout": 30}, key=key)
        message = reply(harness)
        assert message.body["code"] == "provisioning_failed" and "Invalid staged archive" in message.body["message"]
        harness.send("provision", {"spec": {"kind": "uv"}, "source_sha256": source_sha, "timeout": 30}, key=key)
        assert reply(harness).body["code"] == "provisioning_failed"
        config = {
            "reference": "plant.py",
            "metadata_artifacts": True,
            "staged_snapshot": {"source_sha256": source_sha, "inputs_sha256": inputs_sha, "python_path": ["."]},
            "environment": {"spec_hash": "8" * 64, "evidence_sha256": answer["evidence_sha256"]},
        }
        reference = {"sha256": request["sha256"], "length": request["length"]}
        body = {"config": config, "timeout": 30, "request_ref": reference}
        harness.send("run", body, key=key)
        assert reply(harness).body["code"] == "preparation_failed"
        config["environment"]["spec_hash"] = answer["spec_hash"]
        harness.send("run", body, key=key)
        while (message := reply(harness)).type == "diagnostics":
            pass
        # The child read the staged request document and refused it; the attempt directory is gone.
        assert message.type == "error" and "request" in message.body["message"].lower()
        assert list((tmp_path / "cache" / "attempts").iterdir()) == []
    finally:
        harness.close()


def test_a_staged_run_needs_a_cache(tmp_path):
    from simulatte.execution.schema import AttemptKey
    from tests.execution._worker_harness import WorkerHarness
    from tests.execution.test_staging import reply

    harness = WorkerHarness()
    try:
        assert harness.receive().type == "hello"
        body = {"config": {"staged_snapshot": {}}, "timeout": 5}
        harness.send("run", body, key=AttemptKey("a" * 64, "b" * 32, 1))
        message = reply(harness)
        assert message.body["code"] == "preparation_failed" and "staging cache" in message.body["message"]
    finally:
        harness.close()


def test_layout_abort_unrunnable_uv_and_waiting_for_another_build(tmp_path, source, tools):
    import fcntl
    import threading

    uv = install_uv(tools, site="none")
    with pytest.raises(RemoteError) as caught:
        Provisioner(tmp_path / "layout", uv=str(uv)).provision(spec(), source, timeout=30)
    assert caught.value.details["reason"] == "layout"
    broken = tmp_path / "broken-uv"
    broken.write_text("#!/nonexistent/interpreter\n")
    broken.chmod(0o755)
    with pytest.raises(RemoteError) as caught:
        Provisioner(tmp_path / "broken", uv=str(broken)).provision(spec(), source, timeout=30)
    assert caught.value.code == "uv_missing"
    # Supervisor shutdown aborts a running build.
    install_uv(tools, sleep=30)
    provisioner = Provisioner(tmp_path / "abort", uv=str(uv))
    failures = []

    def build():
        try:
            provisioner.provision(spec(), source, timeout=30)
        except RemoteError as error:
            failures.append(error)

    thread = threading.Thread(target=build)
    thread.start()
    deadline = time.monotonic() + 10
    while provisioner._process is None and time.monotonic() < deadline:
        time.sleep(0.02)
    provisioner.abort()
    thread.join(10)
    assert failures and failures[0].details["reason"] == "aborted"
    # A build of the same spec elsewhere holds the lock: wait, then use what it published.
    install_uv(tools, site="dir")
    cache = tmp_path / "shared"
    first = Provisioner(cache, uv=str(uv)).provision(spec(), source, timeout=30)
    calls = len(uv_calls(tools))
    marker = cache / "envs" / first["spec_hash"] / "published"
    (cache / "envs" / first["spec_hash"]).chmod(0o755)
    marker.rename(marker.with_name("hidden"))
    with (cache / "envs" / f"{first['spec_hash']}.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(RemoteError, match="another build") as caught:
            Provisioner(cache, uv=str(uv)).provision(spec(), source, timeout=0.3)
        assert caught.value.details["reason"] == "timeout"
        answers = []
        waiting = threading.Thread(
            target=lambda: answers.append(Provisioner(cache, uv=str(uv)).provision(spec(), source, timeout=30))
        )
        waiting.start()
        time.sleep(0.3)
        marker.with_name("hidden").rename(marker)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    waiting.join(10)
    assert answers == [first] and len(uv_calls(tools)) == calls


def test_slot_config_accepts_distinct_worker_specs():
    from simulatte.execution.workers import WorkerSpec

    worker = WorkerSpec("w", "h", 2, (), None, "~/c", 600.0)
    assert SlotConfig(local=0, workers=[worker]).workers == (worker,)  # ty: ignore[invalid-argument-type]
    for workers in (("w",), (worker, worker)):
        with pytest.raises(ValueError, match="distinct WorkerSpec"):
            SlotConfig(local=0, workers=workers)  # ty: ignore[invalid-argument-type]


def test_local_package_metadata_is_materialized_but_never_their_sources(tmp_path, tools):
    lock = lock_text(
        ("simulatte", _simulatte_version(), PYPI),
        ("plant", "0.1.0", '{ editable = "." }'),
        ("shopkit", "0.1.0", '{ directory = "packages/shopkit" }'),
        ("tool", "0.2.0", '{ editable = "./packages/tool/" }'),
        ("wheel", "1.0", '{ path = "dist/wheel-1.0-py3-none-any.whl" }'),
        ("unused", "1.0", '{ directory = "packages/unused" }'),
        ("outside", "1.0", '{ directory = "../outside" }'),
    ).encode()
    root = PYPROJECT + b'readme = "README.md"\n'
    files = {
        "pyproject.toml": root,
        "uv.lock": lock,
        ".python-version": b"3.14\n",
        "README.md": b"root",
        "plant.py": b"x = 1\n",
        "packages/shopkit/pyproject.toml": (
            b'[project]\nname = "shopkit"\nreadme = { file = "docs/README.rst" }\nlicense-files = ["LICEN[CS]E*", 7]\n'
        ),
        "packages/shopkit/docs/README.rst": b"r",
        "packages/shopkit/LICENSE.txt": b"l",
        "packages/shopkit/NOTICE": b"n",
        "packages/shopkit/src/shopkit/__init__.py": b"",
        "packages/tool/pyproject.toml": (
            b'[project]\nname = "tool"\nlicense = { file = "COPYING" }\nreadme = "MISSING.md"\n'
        ),
        "packages/tool/COPYING": b"c",
        "packages/tool/tool.py": b"",
        "packages/unused/pyproject.toml": b'[project]\nname = "unused"\n',
        "dist/wheel-1.0-py3-none-any.whl": b"wheel",
    }
    blob = archive(tmp_path / "source.tar", files)
    uv = install_uv(tools, site="dir")
    wanted = spec(
        lock_hash=hashlib.sha256(lock).hexdigest(),
        pyproject_hash=hashlib.sha256(root).hexdigest(),
        local_packages=["outside", "plant", "shopkit", "tool", "wheel"],
    )
    Provisioner(tmp_path / "cache", uv=str(uv)).provision(wanted, blob, timeout=30)
    (call,) = uv_calls(tools)
    assert call["files"] == [
        ".python-version",
        "README.md",
        "dist/wheel-1.0-py3-none-any.whl",
        "packages/shopkit/LICENSE.txt",
        "packages/shopkit/docs/README.rst",
        "packages/shopkit/pyproject.toml",
        "packages/tool/COPYING",
        "packages/tool/pyproject.toml",
        "pyproject.toml",
        "uv.lock",
    ]
    assert call["argv"].count("--no-install-package") == 6  # simulatte and every local package stay uninstalled


@pytest.mark.parametrize("member", ["uv.lock", "packages/shopkit/pyproject.toml"])
def test_malformed_lock_or_local_pyproject_is_provisioning_failed(tmp_path, tools, member):
    lock = lock_text(
        ("simulatte", _simulatte_version(), PYPI), ("shopkit", "0.1.0", '{ directory = "packages/shopkit" }')
    )
    files = {"pyproject.toml": PYPROJECT, "uv.lock": lock.encode(), "packages/shopkit/pyproject.toml": b"[project]\n"}
    files[member] = b"this is = = not toml"
    wanted = spec(lock_hash=hashlib.sha256(files["uv.lock"]).hexdigest(), local_packages=["shopkit"])
    with pytest.raises(RemoteError) as caught:
        Provisioner(tmp_path / "cache", uv=str(install_uv(tools))).provision(
            wanted, archive(tmp_path / "source.tar", files), timeout=30
        )
    assert caught.value.code == "provisioning_failed" and caught.value.details["reason"] == "project"
    assert uv_calls(tools) == []


def test_a_bad_staged_tar_is_an_error_not_a_wedge(tmp_path, tools):
    garbage = tmp_path / "garbage.tar"
    garbage.write_bytes(b"\x00not a tar" * 100)
    with pytest.raises(ValueError, match="Invalid staged archive"):
        Provisioner(tmp_path / "cache", uv=str(install_uv(tools))).provision(spec(), garbage, timeout=30)
    with pytest.raises(ValueError, match="Invalid staged archive"):
        extract_archive(garbage, tmp_path / "out")
    with pytest.raises(ValueError, match="Invalid staged archive"):
        archive_names(garbage)


def test_attempt_cleanup_failure_never_raises(tmp_path, monkeypatch):
    from simulatte.execution import provisioning, supervisor

    child = supervisor.ChildProcess.__new__(supervisor.ChildProcess)
    for name in ("process", "connection", "channel", "stream", "attempt_dir"):
        setattr(child, name, None)
    child.pumps, child.diagnostics, child.cleanup_failed = [], supervisor.Diagnostics(), False
    child.remote_dir = tmp_path

    def refuse(path):
        raise PermissionError("read-only cache")

    monkeypatch.setattr(provisioning, "remove_tree", refuse)
    child.close()
    assert child.cleanup_failed and child.remote_dir is None
    assert b"attempt cleanup failed: read-only cache" in child.diagnostics.tail("stderr")
