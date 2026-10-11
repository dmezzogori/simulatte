"""Internal worker dispatch stays independent of optional Studio dependencies."""

import sys

from simulatte import cli
from simulatte.execution.supervisor import Supervisor


def test_internal_worker_dispatch_without_web_imports(monkeypatch):
    monkeypatch.setitem(sys.modules, "starlette", None)
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    monkeypatch.setattr(Supervisor, "run_stdio", lambda self: 7)
    assert cli.main(["_worker"]) == 7
