"""Core-only CLI stays usable without Studio dependencies."""

from unittest.mock import patch
import pytest
from simulatte.cli import main


def test_cli_optional_dependency_and_module(monkeypatch, capsys):
    import runpy
    import sys

    with patch.dict(sys.modules, {"simulatte.studio.server": None}):
        assert main(["view", "run.simtrace"]) == 2
    assert "simulatte[studio]" in capsys.readouterr().err
    with patch("simulatte.cli.main", return_value=0), pytest.raises(SystemExit) as result:
        runpy.run_module("simulatte", run_name="__main__")
    assert result.value.code == 0


def test_parsed_worker_command_starts_supervisor():
    with patch("simulatte.execution.supervisor.Supervisor.run_stdio", return_value=7):
        assert main(["worker", "--stdio", "--stdio"]) == 7


def test_help_without_optional_libraries():
    import sys

    with patch.dict(sys.modules, {"starlette": None, "uvicorn": None}), pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0


def test_studio_directory_cli_and_hidden_worker(monkeypatch, capsys):
    from simulatte.studio import command

    calls = []
    monkeypatch.setattr(command, "serve_studio", lambda args: calls.append(args))
    assert main(["studio", "--no-browser"]) == 0
    assert calls[-1].model is None
    assert main(["studio", "nested/project"]) == 0
    assert calls[-1].model == "nested/project"
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "framed execution supervisor" not in capsys.readouterr().out
