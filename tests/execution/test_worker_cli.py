"""Internal worker dispatch stays independent of optional Studio dependencies."""

import sys

import pytest

from simulatte import cli
from simulatte.execution.supervisor import Supervisor


def test_internal_worker_dispatch_without_web_imports(monkeypatch):
    monkeypatch.setitem(sys.modules, "starlette", None)
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    monkeypatch.setattr(Supervisor, "run_stdio", lambda self: 7)
    assert cli.main(["_worker"]) == 7


def test_remote_worker_dispatch_reports_the_trusted_runtime_hash(monkeypatch):
    from simulatte.execution.supervisor import worker_hello

    monkeypatch.setattr(Supervisor, "run_stdio", lambda self: self.trusted_runtime_hash)
    assert cli.main(["_worker", "a" * 64]) == "a" * 64
    with pytest.raises(ValueError):
        cli.main(["_worker", "not-a-hash"])
    assert worker_hello()["protocol"] == [1, 1] and "trusted_runtime_hash" not in worker_hello()
    assert worker_hello("a" * 64)["trusted_runtime_hash"] == "a" * 64
