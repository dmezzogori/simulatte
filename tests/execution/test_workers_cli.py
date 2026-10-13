"""``simulatte workers``: check_worker and setup_worker against a fake ``ssh`` on PATH; the real one is never run."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from simulatte.cli import main
from simulatte.execution import workers
from simulatte.execution.workers import (
    UV_INSTALL_COMMAND,
    WorkerCheck,
    WorkerSpec,
    check_worker,
    run_ssh,
    setup_worker,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="The fake ssh is a POSIX executable script")

FAKE = Path(__file__).with_name("fake_ssh.py")
INSTALL_ARGV = ["sh", "-c", UV_INSTALL_COMMAND]


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ssh"
    script.write_text(f"#!{sys.executable}\n{FAKE.read_text(encoding='utf-8')}", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def calls(fake_ssh):
    path = fake_ssh / "calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def installs(fake_ssh):
    return [call for call in calls(fake_ssh) if "astral.sh" in call["remote"][-1]]


def spec(host, **fields):
    return WorkerSpec(
        name=host,
        host=host,
        slots=1,
        ssh_args=(),
        uv=None,
        cache="~/.cache/simulatte",
        provision_timeout=600.0,
        **fields,
    )


def test_check_reports_linux_with_uv_in_one_ssh_call(fake_ssh):
    result = check_worker(spec("linux-uv"))
    assert result == WorkerCheck(
        reachable=True, platform="Linux x86_64", shell="/bin/bash", uv_version="0.5.1", cache_writable=True, messages=()
    )
    (call,) = calls(fake_ssh)
    assert call["argv"][:6] == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ClearAllForwardings=yes"]
    assert call["argv"][6:8] == ["linux-uv", "--"] and call["remote"][:2] == ["sh", "-c"]
    probe = call["remote"][2]
    for needle in ("uname -s", "uname -m", '"$uvbin" --version', ".local/bin/uv", ".cargo/bin/uv", ".cache/simulatte"):
        assert needle in probe


def test_check_passes_ssh_args_before_the_host_and_expands_remote_paths(fake_ssh):
    custom = WorkerSpec("w", "me@linux-uv", 2, ("-p", "2222"), "~/bin/uv", "/data/it's", 60.0)
    check_worker(custom)
    (call,) = calls(fake_ssh)
    assert call["argv"][6:] == ["-p", "2222", "me@linux-uv", "--", "sh", "-c", shlex.quote(call["remote"][2])]
    probe = call["remote"][2]
    assert '"$HOME"/bin/uv' in probe and "'/data/it'\"'\"'s'" in probe


def test_check_reports_linux_without_uv(fake_ssh):
    result = check_worker(spec("linux-nouv"))
    assert (result.reachable, result.platform, result.uv_version, result.cache_writable) == (
        True,
        "Linux x86_64",
        None,
        True,
    )
    assert installs(fake_ssh) == []


def test_check_reports_darwin_and_unwritable_cache(fake_ssh):
    darwin = check_worker(spec("darwin-uv"))
    assert (darwin.platform, darwin.shell, darwin.uv_version) == ("Darwin arm64", "/bin/zsh", "0.5.1")
    readonly = check_worker(spec("readonly"))
    assert readonly.reachable and readonly.cache_writable is False
    assert readonly.messages == ("Cache directory ~/.cache/simulatte is not writable on the host",)


def test_check_refuses_windows_hosts(fake_ssh):
    result = check_worker(spec("msys"))
    assert result.reachable is False
    assert result.platform == "MSYS_NT-10.0-19045 x86_64"
    assert result.messages == ("Windows hosts are not supported as workers",)


@pytest.mark.parametrize("system", ["MINGW64_NT-10.0", "CYGWIN_NT-10.0", "Windows_NT"])
def test_windows_family_names_are_refused(system):
    def ssh(args, *, timeout):
        return 0, f"os={system}\narch=x86_64\nshell=sh\nuv=missing\ncache=writable\n", ""

    assert check_worker(spec("w"), ssh=ssh).messages == ("Windows hosts are not supported as workers",)


def test_check_reports_connection_failure_with_bounded_stderr(fake_ssh):
    down = check_worker(spec("down"))
    assert down.reachable is False and down.platform is None and down.uv_version is None and down.cache_writable is None
    assert down.messages == ("ssh: connect to host down port 22: Connection refused",)
    noisy = check_worker(spec("noisy"))
    assert noisy.reachable is False and len(noisy.messages[0].encode()) == 4096


def test_check_reports_unparseable_output(fake_ssh):
    result = check_worker(spec("garbage"))
    assert result.reachable is False and result.messages == ("Unexpected output from the host probe",)


def test_check_survives_missing_client_and_timeouts():
    def missing(args, *, timeout):
        raise FileNotFoundError("ssh")

    def slow(args, *, timeout):
        raise subprocess.TimeoutExpired(args, timeout)

    assert check_worker(spec("w"), ssh=missing).messages == ("Cannot run ssh: ssh",)
    result = check_worker(spec("w"), ssh=slow)
    assert result.reachable is False and result.messages == ("ssh timed out",)


def test_probe_runs_under_a_posix_shell(tmp_path):
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    uv = home / "bin" / "uv"
    uv.write_text("#!/bin/sh\necho 'uv 9.9.9 (test)'\n")
    uv.chmod(0o755)
    probe_spec = WorkerSpec("w", "h", 1, (), "~/bin/uv", "~/cache dir", 600.0)

    def local(args, *, timeout):
        command = shlex.split(" ".join(args[args.index("--") + 1 :]))
        done = subprocess.run(command, capture_output=True, text=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
        return done.returncode, done.stdout, done.stderr

    result = check_worker(probe_spec, ssh=local)
    assert result.reachable and result.uv_version == "9.9.9" and result.cache_writable is True
    assert result.platform == f"{os.uname().sysname} {os.uname().machine}"
    assert (home / "cache dir").is_dir() and list((home / "cache dir").iterdir()) == []
    plain = check_worker(WorkerSpec("w", "h", 1, (), "~/nowhere/uv", "~/cache dir", 600.0), ssh=local)
    assert plain.reachable and plain.cache_writable is True
    home_cache = check_worker(WorkerSpec("w", "h", 1, (), None, "~", 600.0), ssh=local)
    assert home_cache.reachable and home_cache.cache_writable is True
    assert not any(home.glob(".simulatte-probe*"))


def test_ssh_environment_is_an_allowlist(fake_ssh, monkeypatch):
    monkeypatch.setenv("UV_INDEX_URL", "https://user:pw@index.example/simple")
    monkeypatch.setenv("SIMULATTE_TEST_SECRET", "hunter2")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "nope")
    check_worker(spec("linux-uv"))
    (call,) = calls(fake_ssh)
    names = set(call["env"]) - {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    assert names <= {"PATH", "HOME", "USER", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "TERM", "LANG", "LC_ALL"}
    assert call["env"]["SSH_AUTH_SOCK"] == "/tmp/agent.sock" and call["env"]["LC_ALL"] == "C"
    assert "UV_INDEX_URL" not in call["env"] and "SIMULATTE_TEST_SECRET" not in call["env"]
    assert workers.ssh_environment({"FOO": "1", "LC_X": "y", "TERM": "t"}) == {"LC_X": "y", "TERM": "t"}


def test_run_ssh_wraps_subprocess_run(fake_ssh):
    code, out, err = run_ssh(["ssh", "-o", "X=1", "linux-uv", "--", "sh", "-c", "true"], timeout=30)
    assert code == 0 and "os=Linux" in out and err == ""
    code, out, err = run_ssh(["ssh", "down", "--", "true"], timeout=30)
    assert code == 255 and "Connection refused" in err


def test_setup_without_install_flag_only_reports(fake_ssh):
    said = []

    def refuse(prompt):
        raise AssertionError("must not prompt")

    result = setup_worker(spec("linux-nouv"), confirm=refuse, say=said.append)
    assert result.uv_version is None
    assert len(calls(fake_ssh)) == 1
    text = "\n".join(said)
    assert "uv: missing" in text and "--install-uv" in text


def test_setup_declined_install_runs_no_installer(fake_ssh):
    said, prompts = [], []

    def decline(prompt):
        prompts.append(prompt)
        return "n"

    result = setup_worker(spec("linux-nouv"), install_uv=True, confirm=decline, say=said.append)
    assert prompts == ["Install uv on linux-nouv with the official installer? [y/N] "]
    assert result.uv_version is None and "uv not installed" in said
    assert installs(fake_ssh) == [] and len(calls(fake_ssh)) == 1


@pytest.mark.parametrize("answer", ["", "maybe", "yep", " no "])
def test_setup_any_other_answer_is_no(fake_ssh, answer):
    setup_worker(spec("linux-nouv"), install_uv=True, confirm=lambda prompt: answer, say=lambda line: None)
    assert installs(fake_ssh) == []


def test_setup_non_interactive_counts_as_no(fake_ssh):
    def eof(prompt):
        raise EOFError

    said = []
    setup_worker(spec("linux-nouv"), install_uv=True, confirm=eof, say=said.append)
    assert installs(fake_ssh) == [] and "uv not installed" in said


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", " Yes\n"])
def test_setup_installs_exactly_the_constant_command_once(fake_ssh, answer):
    result = setup_worker(spec("linux-nouv"), install_uv=True, confirm=lambda prompt: answer, say=lambda line: None)
    (install,) = installs(fake_ssh)
    assert install["remote"] == INSTALL_ARGV
    assert install["argv"][-4:] == ["--", "sh", "-c", shlex.quote(UV_INSTALL_COMMAND)]
    assert UV_INSTALL_COMMAND == "curl -LsSf https://astral.sh/uv/install.sh | sh"
    assert result.uv_version == "0.5.1" and len(calls(fake_ssh)) == 3  # probe, install, re-check


def test_setup_does_not_install_when_uv_is_present_or_host_unusable(fake_ssh):
    def refuse(prompt):
        raise AssertionError("must not prompt")

    for host in ("linux-uv", "down", "msys"):
        setup_worker(spec(host), install_uv=True, confirm=refuse, say=lambda line: None)
    assert installs(fake_ssh) == []


def test_setup_reports_a_failed_installer(fake_ssh):
    said = []
    result = setup_worker(spec("broken-install"), install_uv=True, confirm=lambda prompt: "y", say=said.append)
    assert result.uv_version is None
    assert result.messages[0].startswith("uv installer failed (exit 1): curl: (22)")
    assert len(installs(fake_ssh)) == 1


def test_setup_survives_installer_timeouts():
    state = {"probes": 0}

    def ssh(args, *, timeout):
        if "astral.sh" in args[-1]:
            raise subprocess.TimeoutExpired(args, timeout)
        state["probes"] += 1
        return 0, "os=Linux\narch=x86_64\nshell=sh\nuv=missing\ncache=writable\n", ""

    result = setup_worker(spec("w"), install_uv=True, confirm=lambda prompt: "y", ssh=ssh, say=lambda line: None)
    assert result.messages == ("uv installer failed (exit 255): ssh timed out",) and state["probes"] == 2


# ----- command line -----


@pytest.fixture
def configured(tmp_path, monkeypatch, fake_ssh):
    path = tmp_path / "workers.toml"
    hosts = ["linux-uv", "linux-nouv", "readonly", "down", "msys", "darwin-uv"]
    path.write_text("".join(f'[workers.{h}]\nhost = "{h}"\nslots = 2\n' for h in hosts), encoding="utf-8")
    monkeypatch.setenv("SIMULATTE_WORKERS", str(path))
    return path


def run_cli(capsys, *argv):
    code = main(["workers", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_list(configured, capsys):
    code, out, err = run_cli(capsys, "list")
    assert code == 0 and err == ""
    assert out.splitlines()[0].split() == ["linux-uv", "linux-uv", "2"]
    code, out, _ = run_cli(capsys, "list", "--json")
    document = json.loads(out)
    assert code == 0 and document["status"] == "succeeded"
    assert document["workers"][1] == {"name": "linux-nouv", "host": "linux-nouv", "slots": 2}


def test_cli_list_with_no_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SIMULATTE_WORKERS", str(tmp_path / "missing.toml"))
    code, out, err = run_cli(capsys, "list")
    assert (code, out) == (0, "") and "No workers configured" in err


def test_cli_check_exit_codes_and_json_envelope(configured, capsys, fake_ssh):
    code, out, err = run_cli(capsys, "check", "linux-uv", "--json")
    assert code == 0 and err == ""
    assert json.loads(out) == {
        "name": "linux-uv",
        "host": "linux-uv",
        "reachable": True,
        "platform": "Linux x86_64",
        "shell": "/bin/bash",
        "uv_version": "0.5.1",
        "cache_writable": True,
        "messages": [],
    }
    code, out, _ = run_cli(capsys, "check", "linux-nouv", "--json")
    assert code == 1 and json.loads(out)["uv_version"] is None
    assert installs(fake_ssh) == []
    assert run_cli(capsys, "check", "readonly", "--json")[0] == 1
    code, out, _ = run_cli(capsys, "check", "down", "--json")
    assert code == 2 and json.loads(out)["reachable"] is False
    assert run_cli(capsys, "check", "msys", "--json")[0] == 2


def test_cli_check_human_report_does_not_install(configured, capsys, fake_ssh):
    code, out, err = run_cli(capsys, "check", "linux-nouv")
    assert code == 1 and err == ""
    assert "worker linux-nouv (linux-nouv)" in out and "uv: missing" in out and "cache: writable" in out
    assert installs(fake_ssh) == []
    code, out, _ = run_cli(capsys, "check", "down")
    assert code == 2 and "reachable: no" in out and "Connection refused" in out
    code, out, _ = run_cli(capsys, "check", "readonly")
    assert code == 1 and "cache: not writable" in out


def test_cli_unknown_worker_and_config_errors_exit_2(configured, tmp_path, monkeypatch, capsys):
    code, out, err = run_cli(capsys, "check", "nope")
    assert (code, out) == (2, "") and err.strip() == (
        "Unknown worker 'nope'; known workers: darwin-uv, down, linux-nouv, linux-uv, msys, readonly"
    )
    code, out, _ = run_cli(capsys, "check", "nope", "--json")
    assert code == 2 and json.loads(out)["status"] == "validation_failed"
    bad = tmp_path / "bad.toml"
    bad.write_text('[workers.a]\nhost = "h"\npassword = "x"\n', encoding="utf-8")
    monkeypatch.setenv("SIMULATTE_WORKERS", str(bad))
    for verb in ("list", "check a", "setup a"):
        code, out, err = run_cli(capsys, *verb.split())
        assert code == 2 and "credentials are not accepted in workers.toml" in err
    assert run_cli(capsys, "setup", "a", "--json")[0] == 2


def test_cli_setup_reports_without_flag_and_installs_on_yes(configured, capsys, fake_ssh, monkeypatch):
    code, out, err = run_cli(capsys, "setup", "linux-nouv")
    assert code == 1 and "--install-uv" in out and installs(fake_ssh) == []
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    code, out, err = run_cli(capsys, "setup", "linux-nouv", "--install-uv")
    assert code == 1 and "uv not installed" in out and installs(fake_ssh) == []
    answers = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    code, out, err = run_cli(capsys, "setup", "linux-nouv", "--install-uv")
    assert code == 0 and "uv: 0.5.1" in out and len(installs(fake_ssh)) == 1


def test_cli_setup_json_keeps_stdout_machine_readable(configured, capsys, fake_ssh, monkeypatch):
    prompts = []

    def answer(prompt=""):
        prompts.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", answer)
    code, out, err = run_cli(capsys, "setup", "linux-nouv", "--install-uv", "--json")
    document = json.loads(out)
    assert code == 0 and document["uv_version"] == "0.5.1" and document["name"] == "linux-nouv"
    assert "Install uv on linux-nouv" in err and prompts == [""]
    code, out, _ = run_cli(capsys, "setup", "down", "--json")
    assert code == 2 and json.loads(out)["reachable"] is False


def test_cli_setup_without_a_terminal_declines(configured, capsys, fake_ssh, monkeypatch):
    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    code, out, _ = run_cli(capsys, "setup", "linux-nouv", "--install-uv")
    assert code == 1 and installs(fake_ssh) == []


def test_prune_removes_unreferenced_blobs_and_environments(tmp_path, monkeypatch, fake_ssh, capsys):
    home = tmp_path / "home"
    cache = home / "cache dir"
    monkeypatch.setenv("HOME", str(home))
    path = tmp_path / "workers.toml"
    path.write_text('[workers.here]\nhost = "local-sh"\ncache = "~/cache dir"\n[workers.down]\nhost = "down"\n')
    monkeypatch.setenv("SIMULATTE_WORKERS", str(path))
    used, unused = "a" * 64, "b" * 64
    for name in (used, unused, f"{unused}.0011.part"):
        (cache / "blobs").mkdir(parents=True, exist_ok=True)
        (cache / "blobs" / name).write_text("x")
        (cache / "blobs" / name).chmod(0o444)
    for name in ("e1", "e2", "e2.build"):
        (cache / "envs" / name / "lib").mkdir(parents=True)
        (cache / "envs" / name / "lib" / "file").write_text("x")
        for item in (cache / "envs" / name / "lib" / "file", cache / "envs" / name / "lib", cache / "envs" / name):
            item.chmod(0o555 if item.is_dir() else 0o444)
    (cache / "envs" / "e2.lock").write_text("")
    (cache / "attempts" / "x").mkdir(parents=True)
    (cache / "attempts" / "x" / "refs").write_text(f"{used}\ne1\n")
    expected = [f"blobs/{unused}", f"blobs/{unused}.0011.part", "envs/e2", "envs/e2.build", "envs/e2.lock"]
    code, out, _ = run_cli(capsys, "prune", "here", "--dry-run", "--json")
    document = json.loads(out)
    assert code == 0 and document["status"] == "succeeded" and document["dry_run"] is True
    assert sorted(document["removed"]) == expected and (cache / "envs" / "e2").is_dir()
    code, out, _ = run_cli(capsys, "prune", "here")
    assert code == 0 and sorted(out.splitlines()) == sorted(f"removed {item}" for item in expected)
    assert sorted(p.name for p in (cache / "blobs").iterdir()) == [used]
    assert sorted(p.name for p in (cache / "envs").iterdir()) == ["e1"]
    code, out, _ = run_cli(capsys, "prune", "here", "--dry-run")
    assert (code, out.strip()) == (0, "nothing to remove")
    code, out, err = run_cli(capsys, "prune", "down")
    assert code == 2 and "Connection refused" in err
    code, out, _ = run_cli(capsys, "prune", "down", "--json")
    assert code == 2 and json.loads(out)["status"] == "unreachable"
    from simulatte.execution.provisioning import remove_tree

    remove_tree(cache)  # Read-only trees would trouble pytest's temporary-directory cleanup.
