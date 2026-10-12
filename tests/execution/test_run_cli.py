"""Public headless command uses captured runs without importing web libraries."""

import json
import sys
from unittest.mock import patch

import pytest

from simulatte.cli import main
from tests.execution.test_coordinator import project


def test_run_json_and_reproduce_without_working_model_or_web(tmp_path, capsys):
    spec = project(tmp_path)
    model = spec.root / "plant.py"
    model.write_text('import sys\nprint("model stdout")\nprint("model stderr", file=sys.stderr)\n' + model.read_text())
    output = tmp_path / "result.simtrace"
    with patch.dict(sys.modules, {"starlette": None, "uvicorn": None}):
        assert main(["run", str(spec.root / "plant.py"), "--param", "n=4", "--json", "--output", str(output)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "succeeded"
        assert output.is_file()
        (spec.root / "plant.py").unlink()
        (spec.root / "pyproject.toml").write_text("broken toml[")
        assert main(["run", "--reproduce", result["request_id"], "--project", str(spec.root), "--json"]) == 0
        repeated = json.loads(capsys.readouterr().out)
        assert repeated["request_id"] == result["request_id"]
        assert repeated["attempt_id"] != result["attempt_id"]


def test_run_never_overwrites_export(tmp_path, capsys):
    spec = project(tmp_path)
    output = tmp_path / "result.simtrace"
    output.write_text("keep")
    assert main(["run", str(spec.root / "plant.py"), "--output", str(output), "--json"]) == 2
    assert output.read_text() == "keep"
    assert json.loads(capsys.readouterr().out)["status"] == "validation_failed"


@pytest.mark.parametrize("options", [["--param", "n=1", "--param", "n=2"], ["--param", "n=true"], ["--warmup", "5"]])
def test_run_invalid_parameters_and_stopping(tmp_path, capsys, options):
    spec = project(tmp_path)
    assert main(["run", str(spec.root / "plant.py"), "--json", *options]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "validation_failed"


@pytest.mark.parametrize(
    "options", [[], ["model.py", "--reproduce", "a" * 64], ["--reproduce", "a" * 64, "--param", "x=1"]]
)
def test_invalid_command_combinations_produce_one_json_envelope(options, capsys):
    assert main(["run", "--json", *options]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "validation_failed"


@pytest.mark.parametrize(
    "error,exit_code,status", [(OSError("disk error"), 1, "failed"), (KeyboardInterrupt(), 130, "cancelled")]
)
def test_headless_setup_failure_has_bounded_json(monkeypatch, capsys, error, exit_code, status):
    from simulatte.execution import command

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(command.Coordinator, "from_store", fail)
    assert main(["run", "--reproduce", "a" * 64, "--json"]) == exit_code
    assert json.loads(capsys.readouterr().out)["status"] == status


@pytest.mark.parametrize(
    "options,expected",
    [
        (["--horizon", "5", "--warmup", "1"], {"type": "horizon", "horizon": 5.0, "warmup": 1.0}),
        (
            ["--arrivals", "0", "--safety-limit", "4"],
            {"type": "finite_population", "arrivals": 0, "cutoff": None, "safety_limit": 4.0},
        ),
        (
            ["--cutoff", "2", "--safety-limit", "4"],
            {"type": "finite_population", "arrivals": None, "cutoff": 2.0, "safety_limit": 4.0},
        ),
    ],
)
def test_cli_stopping_overrides_are_normalized(options, expected):
    import argparse
    from simulatte.execution.command import add_arguments, _stopping

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    assert _stopping(parser.parse_args(options), {"type": "horizon", "horizon": 2, "warmup": 0}) == expected


@pytest.mark.parametrize(
    "options",
    [["--arrivals", "1"], ["--safety-limit", "4"], ["--arrivals", "1", "--safety-limit", "4", "--warmup", "0"]],
)
def test_incompatible_stopping_options_are_refused(options):
    import argparse
    from simulatte.execution.command import add_arguments, _stopping

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    with pytest.raises(ValueError):
        _stopping(parser.parse_args(options), {"type": "horizon", "horizon": 2, "warmup": 0})


def test_failed_export_removes_only_its_partial_file(tmp_path, monkeypatch):
    from simulatte.execution import command

    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"trace")

    def fail(source, output, length):
        output.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(command.shutil, "copyfileobj", fail)
    with pytest.raises(OSError):
        command._export(source, target)
    assert not target.exists()
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        command._export(source, target)
    assert target.read_bytes() == b"existing"


def test_missing_reproduction_store_is_execution_failure_without_creating_store(tmp_path, capsys):
    assert main(["run", "--reproduce", "a" * 64, "--project", str(tmp_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    assert not (tmp_path / ".simulatte").exists()


@pytest.mark.parametrize("status,code", [("validation_failed", 2), ("failed", 1), ("cancelled", 130)])
def test_terminal_failure_exit_codes(monkeypatch, capsys, status, code):
    from simulatte.execution import command
    from simulatte.execution.schema import AttemptKey

    def fail(*args, **kwargs):
        raise command.ExecutionFailure(AttemptKey("a" * 64, "b" * 32, 1), status, {"message": "failure"})

    monkeypatch.setattr(command.Coordinator, "from_store", fail)
    assert main(["run", "--reproduce", "a" * 64, "--json"]) == code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_shutdown_failure_replaces_success_and_human_output_uses_stderr(monkeypatch, capsys):
    from types import SimpleNamespace
    from simulatte.execution import command

    def close():
        raise RuntimeError("cleanup failed")

    owner = SimpleNamespace(
        close=close, reproduce=lambda *a, **kw: (_ for _ in ()).throw(ValueError("missing snapshot"))
    )
    monkeypatch.setattr(command.Coordinator, "from_store", lambda *a: owner)
    assert main(["run", "--reproduce", "a" * 64]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "cleanup failed" in output.err


def test_keyboard_interrupt_cancels_assigned_attempt(monkeypatch, capsys):
    from types import SimpleNamespace
    from simulatte.execution import command
    from simulatte.execution.schema import AttemptKey

    key = AttemptKey("a" * 64, "b" * 32, 1)
    cancelled = []
    prepared = SimpleNamespace(
        revision=None,
        metadata={
            "selected": "build",
            "models": [
                {
                    "name": "build",
                    "parameters": {"version": 1, "fields": []},
                    "stopping": {"type": "horizon", "horizon": 1, "warmup": 0},
                }
            ],
        },
    )

    def interrupt(*args):
        raise KeyboardInterrupt()

    owner = SimpleNamespace(
        prepare=lambda **kw: prepared,
        make_request=lambda *a, **kw: None,
        submit=lambda *a, **kw: key,
        wait=interrupt,
        cancel=cancelled.append,
        close=lambda: None,
    )
    monkeypatch.setattr(command, "discover_project", lambda *a, **kw: SimpleNamespace(model="plant.py"))
    monkeypatch.setattr(command, "Coordinator", lambda *a, **kw: owner)
    assert main(["run", "plant.py:build", "--json"]) == 130
    assert cancelled == [key]
    assert json.loads(capsys.readouterr().out)["status"] == "cancelled"
    assert main(["run", "plant.py:build", "--param", "missing_equals", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "validation_failed"
