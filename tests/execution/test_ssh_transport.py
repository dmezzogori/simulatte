"""SSH transport and remote bootstrap against a fake ``ssh`` on PATH; the real one is never run."""

from dataclasses import replace
import functools
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

import pytest

from simulatte.execution import coordinator as m, schema
from simulatte.execution.bootstrap_remote import MAX_SCRIPT, bootstrap_script, probe_script, stage_script
from simulatte.execution.snapshot import ProjectSpec, freeze_runtime
from simulatte.execution.ssh import BootstrapError, SshTransport
from simulatte.execution.store import INTERACTIVE
from simulatte.execution.workers import WorkerSpec
from tests.execution.test_schema import request
from tests.execution.test_workers_cli import calls, fake_ssh  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="SSH from Windows is not supported in 0.17")

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "simulatte"
PYTHON = "3.14.1"
FAKE_UV = """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with (Path(__file__).resolve().parent / "uv.jsonl").open("a") as log:
    log.write(json.dumps({"argv": args, "env": dict(os.environ)}) + "\\n")
command = args[args.index("--") + 1 :]
assert command[0] == "python"
os.execv(sys.executable, [sys.executable, *command[1:]])
"""


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    project = tmp_path_factory.mktemp("runtime-project")
    return freeze_runtime(PACKAGE, project)


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


def fake_uv(directory: Path) -> Path:
    path = directory / "fake-uv"
    path.write_text(f"#!{sys.executable}\n{FAKE_UV}", encoding="utf-8")
    path.chmod(0o755)
    return path


def worker(host, *, uv=None, cache="~/it's a cache"):
    return WorkerSpec(host, host, 1, (), None if uv is None else str(uv), cache, 600.0)


def role(call):
    script = call["remote"][-1]
    return "probe" if script.startswith("test -f") else "stage" if "head -c" in script else "session"


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.02)


# ----- frozen runtime -----


def test_frozen_runtime_archive_imports_the_supervisor_in_an_isolated_child(runtime, tmp_path):
    assert runtime.archive.is_file() and len(runtime.archive_sha256) == 64
    assert dict(runtime.supervisor_distributions).keys() == {"msgpack", "simpy"}
    target = tmp_path / "extracted"
    with tarfile.open(runtime.archive) as archive:
        names = archive.getnames()
        assert names == sorted(names)
        assert all(member.uid == member.gid == member.mtime == 0 for member in archive)
        assert not any(name.endswith((".so", ".pyd", ".pyc")) for name in names)
        archive.extractall(target, filter="data")
    code = (
        "import sys; root=sys.argv[1]; sys.path[:0]=[root, root+'/supervisor-deps']\n"
        "import msgpack, simpy, simulatte.cli, simulatte.execution.supervisor\n"
        "assert msgpack.Packer.__module__ == 'msgpack.fallback', msgpack.Packer\n"
        "assert not hasattr(msgpack, '_cmsgpack')\n"
        "for module in (msgpack, simpy, simulatte.cli):\n"
        "    assert module.__file__.startswith(root), module.__file__\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(target)], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0, done.stderr
    # The deps root is supervisor-only; the trusted runtime root itself holds simulatte alone.
    assert sorted(path.name for path in runtime.root.iterdir()) == ["simulatte"]
    assert (runtime.supervisor_deps / "msgpack" / "fallback.py").is_file()
    assert not list(runtime.supervisor_deps.rglob("_cmsgpack*"))


# ----- remote scripts -----


def test_remote_scripts_are_bounded_quoted_and_validated():
    sha = "a" * 64
    for text in (
        probe_script("~/c", sha),
        stage_script("~/it's", sha, 10),
        bootstrap_script("~/it's", sha, PYTHON, "~/bin/u v", "w1", "b" * 64),
    ):
        assert len(text.encode()) <= MAX_SCRIPT
        assert "'\"'\"'" in text or text.startswith("test -f")
    with pytest.raises(ValueError):
        stage_script("~/c", "not-a-hash", 10)
    with pytest.raises(ValueError):
        stage_script("~/c", sha, -1)
    with pytest.raises(ValueError):
        bootstrap_script("~/c", sha, "3.14; rm -rf /", None, "w1", sha)
    with pytest.raises(ValueError):
        bootstrap_script("x" * MAX_SCRIPT, sha, PYTHON, None, "w1", sha)
    with pytest.raises(ValueError):
        bootstrap_script("~/c", sha, PYTHON, None, "two words", sha)
    with pytest.raises(ValueError):
        probe_script(" ", sha)


def test_bootstrap_reports_missing_uv_and_missing_runtime(tmp_path):
    environment = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    text = bootstrap_script("~/cache", "a" * 64, PYTHON, None, "w1", "b" * 64)
    done = subprocess.run(["/bin/sh", "-c", text], env=environment, capture_output=True, text=True, timeout=10)
    assert done.returncode == 3
    assert "simulatte: uv is missing; run: simulatte workers setup w1 --install-uv" in done.stderr
    text = bootstrap_script("~/cache", "a" * 64, PYTHON, str(fake_uv(tmp_path)), "w1", "b" * 64)
    done = subprocess.run(["/bin/sh", "-c", text], env=environment, capture_output=True, text=True, timeout=10)
    assert done.returncode == 4 and "not staged" in done.stderr


# ----- staging and bootstrap through the fake ssh -----


def test_staging_verifies_the_hash_and_is_skipped_when_present(fake_ssh, home, runtime, tmp_path):  # noqa: F811
    spec = worker("local-sh", uv=fake_uv(tmp_path))
    for _ in range(2):
        transport = SshTransport.start(spec, runtime=runtime, python=PYTHON)
        try:
            hello = transport.receive(10)
            assert hello.type == "hello" and list(hello.body["protocol"]) == [1, 1]
            assert hello.body["trusted_runtime_hash"] == runtime.content_hash
        finally:
            transport.close()
        assert transport.process.returncode is not None
    # The second start finds the hash present and streams nothing.
    assert [role(call) for call in calls(fake_ssh)] == ["probe", "stage", "session", "probe", "session"]
    staged = home / "it's a cache" / "runtime" / runtime.archive_sha256
    assert (staged / "ready").is_file() and (staged / "simulatte" / "cli.py").is_file()
    assert (staged / "supervisor-deps" / "msgpack" / "fallback.py").is_file()
    assert [path.name for path in staged.parent.iterdir()] == [runtime.archive_sha256]
    uv = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert uv[0]["argv"][:6] == ["run", "--no-project", "--python", PYTHON, "--no-sync", "--"]


def test_staging_rejects_a_hash_mismatch_and_leaves_no_partial_file(fake_ssh, home, runtime, tmp_path):  # noqa: F811
    tampered = replace(runtime, archive_sha256="0" * 64)
    with pytest.raises(BootstrapError) as caught:
        SshTransport.start(worker("local-sh", uv=fake_uv(tmp_path)), runtime=tampered, python=PYTHON)
    assert caught.value.code == "runtime_hash_mismatch" and "hash mismatch" in str(caught.value)
    assert [role(call) for call in calls(fake_ssh)] == ["probe", "stage"]
    assert list((home / "it's a cache" / "runtime").iterdir()) == []


def test_ssh_environment_and_argv_follow_the_allowlist(fake_ssh, home, runtime, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("UV_INDEX_URL", "https://user:pw@index.example/simple")
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "secret")
    monkeypatch.setenv("SIMULATTE_TEST_SECRET", "hunter2")
    monkeypatch.setenv("MSGPACK_PUREPYTHON", "1")
    transport = SshTransport.start(worker("local-sh", uv=fake_uv(tmp_path)), runtime=runtime, python=PYTHON)
    try:
        assert transport.receive(10).type == "hello"
    finally:
        transport.close()
    recorded = calls(fake_ssh)
    assert len(recorded) == 3
    uv = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    allowed = {"PATH", "HOME", "USER", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "TERM", "LANG", "__CF_USER_TEXT_ENCODING"}
    for environment in [call["env"] for call in recorded] + [call["env"] for call in uv]:
        extra = {name for name in environment if name not in allowed and not name.startswith("LC_")}
        assert extra <= {"PWD", "SHLVL", "_", "OLDPWD"}, extra  # the remote shell's own variables only
        assert not any(name.startswith(("UV_", "SIMULATTE_", "MSGPACK_")) for name in environment)
    for call in recorded:
        argv = call["argv"]
        assert argv[:6] == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ClearAllForwardings=yes"]
        assert argv[argv.index("--") - 1] == "local-sh" and argv[argv.index("--") + 1 : argv.index("--") + 3] == [
            "sh",
            "-c",
        ]


@pytest.mark.parametrize("host,code", [("uv-missing", "uv_missing"), ("runtime-missing", "runtime_missing")])
def test_bootstrap_exit_codes_surface_as_distinct_errors(fake_ssh, home, runtime, host, code):  # noqa: F811
    transport = SshTransport.start(worker(host), runtime=runtime, python=PYTHON)
    try:
        with pytest.raises(BootstrapError) as caught:
            transport.receive(10)
        assert caught.value.code == code
    finally:
        transport.close()
    assert transport.process.returncode in (3, 4)


def test_probe_and_stage_failures_are_bootstrap_errors(fake_ssh, home, runtime, tmp_path, monkeypatch):  # noqa: F811
    with pytest.raises(BootstrapError, match="exit 255") as caught:
        SshTransport.start(worker("down"), runtime=runtime, python=PYTHON)
    assert caught.value.code == "probe_failed"
    with pytest.raises(BootstrapError, match="exit 1") as caught:
        SshTransport.start(worker("local-sh", cache="/dev/null/cache"), runtime=runtime, python=PYTHON)
    assert caught.value.code == "staging_failed"
    real = subprocess.run

    def run(args, **kwargs):
        if kwargs.get("stdin") is subprocess.DEVNULL and stage:
            return SimpleNamespace(returncode=1, stderr=b"")
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    for stage, step in ((False, "probe"), (True, "Staging")):
        monkeypatch.setattr(subprocess, "run", run)
        with pytest.raises(BootstrapError, match=f"{step}.* timed out"):
            SshTransport.start(worker("w"), runtime=runtime, python=PYTHON)
        monkeypatch.setattr(subprocess, "run", real)


# ----- session framing -----


def test_banner_then_valid_hello(fake_ssh, home, runtime):  # noqa: F811
    transport = SshTransport.start(worker("banner-hello"), runtime=runtime, python=PYTHON)
    try:
        hello = transport.receive(10)
        assert hello.type == "hello" and list(hello.body["protocol"]) == [1, 1]
        assert transport.receive(3).type == "heartbeat"
    finally:
        transport.close()
    assert transport.process.returncode is not None


def test_stderr_flood_is_bounded(fake_ssh, home, runtime):  # noqa: F811
    transport = SshTransport.start(worker("stderr-flood"), runtime=runtime, python=PYTHON)
    try:
        assert transport.receive(10).type == "hello"
        wait_for(lambda: transport.diagnostics.take()["dropped_bytes"] >= 9 << 20, timeout=20)
        assert len(transport.diagnostics.tail("stderr")) == transport.diagnostics.TAIL
        assert transport.receive(3).type == "heartbeat"
    finally:
        transport.close()
    assert transport.process.returncode is not None


def test_noise_above_64_kib_before_magic_is_a_protocol_error(fake_ssh, home, runtime):  # noqa: F811
    transport = SshTransport.start(worker("noise-overflow"), runtime=runtime, python=PYTHON)
    try:
        with pytest.raises(OSError, match="noise exceeds limit"):
            transport.receive(10)
    finally:
        transport.close()
    assert transport.process.returncode is not None


# ----- slot actor with the ssh factory -----


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    project = ProjectSpec(tmp_path / "project", "plant.py", "layout.json", (), (), (".",), False)
    project.root.mkdir()
    monkeypatch.setattr(m, "materialize", lambda snapshot, path: SimpleNamespace(root=path, python_path=[path]))
    with m.Coordinator(project) as owner:
        yield owner


def use_ssh(coordinator, host):
    actor = coordinator._actors[INTERACTIVE]
    started = []

    def factory():
        started.append(coordinator._transports["uv"](worker(host)))
        return started[-1]

    actor.transport_factory = factory
    return started


def test_banner_then_hang_is_lost_and_slot_recovers(fake_ssh, home, coordinator):  # noqa: F811
    started = use_ssh(coordinator, "banner-hang")
    revision = coordinator.store.create_revision({})
    began = time.monotonic()
    key = coordinator._schedule(request(schema), revision, None, {}, None)
    with pytest.raises(m.ExecutionFailure) as error:
        coordinator.wait(key, timeout=20)
    assert error.value.status == "lost"
    assert time.monotonic() - began < 12
    wait_for(lambda: started[0].process.returncode is not None, timeout=10)
    # The same slot starts a fresh transport for the next assignment and completes a handshake.
    started = use_ssh(coordinator, "banner-hello")
    key = coordinator._schedule(request(schema, seed=1), revision, None, {}, None)
    with pytest.raises(m.ExecutionFailure) as error:
        coordinator.wait(key, timeout=30)
    assert error.value.status != "lost"
    assert started and started[0].channel.handshake_complete


def test_eof_mid_fragment_marks_lost(fake_ssh, home, coordinator):  # noqa: F811
    started = use_ssh(coordinator, "eof-mid-fragment")
    key = coordinator._schedule(request(schema), coordinator.store.create_revision({}), None, {}, None)
    with pytest.raises(m.ExecutionFailure) as error:
        coordinator.wait(key, timeout=20)
    assert error.value.status == "lost"
    wait_for(lambda: started[0].process.returncode is not None, timeout=10)


class Peer:
    def __init__(self, protocol=(1, 1), failure=None):
        self.protocol, self.failure = protocol, failure
        self.closed = False

    def receive(self, timeout):
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(
            type="hello", body={"protocol": list(self.protocol), "capabilities": ["metadata-artifacts"]}
        )

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "kind,protocol,accepted",
    [
        ("local", (1, 0), True),
        ("local", (1, 1), True),
        ("remote", (1, 1), True),
        ("remote", (1, 0), False),
        ("local", (2, 0), False),
    ],
)
def test_protocol_minor_versions_by_slot_kind(coordinator, kind, protocol, accepted):
    actor = m.SlotActor(coordinator, "w-0", lambda: Peer(protocol), kind=kind, host=None if kind == "local" else "w")
    if accepted:
        assert actor._start_worker() is actor.worker
    else:
        with pytest.raises(ValueError, match="incompatible"):
            actor._start_worker()


@pytest.mark.parametrize("failure", [TimeoutError("hello timeout"), OSError("closed")])
def test_handshake_loss_is_worker_lost(coordinator, failure):
    actor = m.SlotActor(coordinator, "w-0", lambda: Peer(failure=failure), kind="remote", host="w")
    with pytest.raises(m.WorkerLost):
        actor._start_worker()


def test_bootstrap_failures_are_not_handshake_loss(coordinator):
    failure = BootstrapError("uv_missing", "uv is missing")
    actor = m.SlotActor(coordinator, "w-0", lambda: Peer(failure=failure), kind="remote", host="w")
    with pytest.raises(BootstrapError):
        actor._start_worker()


def test_ssh_factory_uses_the_coordinator_runtime_and_python(coordinator, monkeypatch):
    seen = {}
    monkeypatch.setattr(m.SshTransport, "start", lambda spec, **kwargs: seen.update(spec=spec, **kwargs) or "t")
    spec = worker("w")
    assert functools.partial(coordinator._transports["uv"], spec)() == "t"
    assert seen["spec"] is spec and seen["runtime"] is coordinator.runtime
    assert seen["python"] == coordinator.python
