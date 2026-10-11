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


def test_help_without_optional_libraries():
    import sys

    with patch.dict(sys.modules, {"starlette": None, "uvicorn": None}), pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
