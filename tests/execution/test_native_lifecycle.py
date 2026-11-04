"""Native failures retain their primary diagnostic through managed cleanup."""

import pytest

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.snapshot import discover_project

MODEL = """from dataclasses import dataclass
from simulatte import model
from simulatte.experimental.native import NativeProduction, Job
@dataclass(frozen=True)
class Params:
    retention: str = "full"
    kind: str = "cleanup"
@model(params=Params, horizon=4)
def build(env, layout, params):
    class Backend(NativeProduction):
        def advance(self, until):
            super().advance(until)
            if params.kind == "advance" and until > 1:
                raise MemoryError("primary native advance failure")
        def close(self):
            super().close()
            if params.kind == "cleanup":
                raise RuntimeError("secondary cleanup failure")
    backend = Backend(1, retention=params.retention, input_state=lambda: {"seed": 1})
    env.attach_execution_backend(backend)
    def source():
        backend.add(Job(0, (0,), (0.25,), 2))
        yield env.timeout(1)
        if params.kind == "cleanup":
            raise ValueError("primary producer failure")
        yield env.timeout(1)
    env.process(source())
"""


@pytest.mark.parametrize("retention", ["full", "bounded"])
@pytest.mark.parametrize("kind", ["advance", "cleanup"])
def test_primary_error_survives_native_cleanup(tmp_path, retention, kind):
    pytest.importorskip("simulatte_native")
    (tmp_path / "plant.py").write_text(MODEL)
    (tmp_path / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py"]\n')
    spec = discover_project(tmp_path / "plant.py", tmp_path)
    with Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=15)
        request = coordinator.make_request(prepared, parameters=dict(retention=retention, kind=kind), level="kpi")
        key = coordinator.submit(request, prepared.revision, timeout=15)
        with pytest.raises(ExecutionFailure):
            coordinator.wait(key, timeout=20)
        attempt = coordinator.store.attempt(key)
        expected = "primary native advance failure" if kind == "advance" else "primary producer failure"
        assert expected in attempt["details"]["message"]
        if kind == "cleanup":
            assert "secondary cleanup failure" in attempt["details"]["traceback"]
        assert attempt["status"] == "failed"
        with pytest.raises(KeyError):
            coordinator.store.result(key.attempt_id)
