"""Core imports and actionable feature errors without the plot/RL extras."""

from __future__ import annotations

import subprocess
import sys

import pytest

from simulatte import _optional
from simulatte.collectors import ServerTimeSeries, ShopFloorTimeSeries
from simulatte.intralogistics import FleetTimeSeries


def test_core_and_trace_import_without_optional_packages() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys

class WithoutExtras(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'matplotlib', 'gymnasium', 'numpy'}:
            raise ModuleNotFoundError(f'No module named {fullname!r}', name=fullname)

sys.meta_path.insert(0, WithoutExtras())
from simulatte import Environment, Runner, Trace, TraceRecorder
import simulatte.builders
import simulatte.collectors
import simulatte.intralogistics
import simulatte.experimental as experimental
assert 'SimulatteEnv' in dir(experimental)
assert not hasattr(experimental, 'unknown')
assert not {'matplotlib', 'gymnasium', 'numpy'} & sys.modules.keys()
try:
    from simulatte.experimental import SimulatteEnv
except ImportError as error:
    assert "pip install 'simulatte[rl]'" in str(error), str(error)
else:
    raise AssertionError('RL access must require its extra')
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("collector", "method"),
    [(ShopFloorTimeSeries, name) for name in ("plot_wip", "plot_job_count", "plot_throughput", "plot_lateness")]
    + [(ServerTimeSeries, name) for name in ("plot_qt", "plot_ut")]
    + [
        (FleetTimeSeries, name)
        for name in ("plot_fleet_utilization", "plot_pending_orders", "plot_throughput", "plot_inventory")
    ],
)
def test_plot_methods_name_missing_extra(collector, method, monkeypatch) -> None:
    def missing(module):
        raise ModuleNotFoundError("No module named 'matplotlib'", name="matplotlib")

    monkeypatch.setattr(_optional, "import_module", missing)
    with pytest.raises(ImportError, match=r"pip install 'simulatte\[plot\]'"):
        # Every plotting method checks its dependency before touching series data.
        getattr(collector.__new__(collector), method)()


def test_optional_import_does_not_hide_broken_transitive_dependencies(monkeypatch) -> None:
    error = ModuleNotFoundError("No module named 'numpy'", name="numpy")

    def broken(module):
        raise error

    monkeypatch.setattr(_optional, "import_module", broken)
    with pytest.raises(ModuleNotFoundError) as caught:
        _optional.import_optional("matplotlib.pyplot", "plot")
    assert caught.value is error


def test_optional_import_success() -> None:
    assert _optional.import_optional("sys", "plot") is sys


def test_experimental_export_with_rl_extra() -> None:
    pytest.importorskip("gymnasium")
    import simulatte.experimental as experimental
    from simulatte.experimental.gymnasium import SimulatteEnv

    assert experimental.SimulatteEnv is SimulatteEnv
    assert experimental.SimulatteEnv is SimulatteEnv
    assert "SimulatteEnv" in dir(experimental)
    assert not hasattr(experimental, "unknown")
