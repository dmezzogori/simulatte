"""A Windows coordinator says that SSH workers are untested (design 8); other platforms stay silent."""

import json

import pytest

from simulatte.cli import main
from simulatte.execution import workers_cli
from simulatte.execution.workers import WorkerCheck

NOTE = "SSH workers from a Windows coordinator are untested in 0.17"


@pytest.fixture
def host(tmp_path, monkeypatch):
    path = tmp_path / "workers.toml"
    path.write_text('[workers.box]\nhost = "box"\n', encoding="utf-8")
    monkeypatch.setenv("SIMULATTE_WORKERS", str(path))
    ready = WorkerCheck(True, "Linux x86_64", "/bin/sh", "0.5.1", True, ())
    monkeypatch.setattr(workers_cli, "check_worker", lambda spec: ready)
    monkeypatch.setattr(workers_cli, "setup_worker", lambda spec, **options: ready)


def run(capsys, *argv):
    code = main(["workers", *argv])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.parametrize("command", ["check", "setup"])
def test_windows_coordinator_note_is_printed_and_in_the_envelope(host, monkeypatch, capsys, command):
    monkeypatch.setattr(workers_cli, "_WINDOWS_COORDINATOR", True)
    code, out, _ = run(capsys, command, "box", "--json")
    assert code == 0 and json.loads(out)["messages"] == [NOTE]
    code, out, _ = run(capsys, command, "box")
    assert code == 0 and NOTE in out
    if command == "check":  # The check report lists it with the other messages.
        assert f"  {NOTE}" in out.splitlines()


@pytest.mark.parametrize("command", ["check", "setup"])
def test_other_platforms_print_no_note(host, monkeypatch, capsys, command):
    monkeypatch.setattr(workers_cli, "_WINDOWS_COORDINATOR", False)
    code, out, _ = run(capsys, command, "box", "--json")
    assert code == 0 and json.loads(out)["messages"] == []
    assert NOTE not in run(capsys, command, "box")[1]
