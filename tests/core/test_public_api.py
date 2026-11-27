"""The stable entry points exported by the top-level package (spec section 3, D48)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import simulatte
from simulatte.environment import Environment
from simulatte.events import DomainEvent, Event, ObserverEvent
from simulatte.kpi import KPI, Collector
from simulatte.layout import Layout
from simulatte.provenance import Provenance
from simulatte.model import model, get_model_spec, Range, Length, Description
from simulatte.stopping import Horizon, FinitePopulation, Population
from simulatte.runner import Runner
from simulatte.trace import Trace, TraceRecorder

EXPECTED = {
    "model": model,
    "get_model_spec": get_model_spec,
    "Range": Range,
    "Length": Length,
    "Description": Description,
    "Horizon": Horizon,
    "FinitePopulation": FinitePopulation,
    "Population": Population,
    "Collector": Collector,
    "DomainEvent": DomainEvent,
    "Environment": Environment,
    "Event": Event,
    "KPI": KPI,
    "Layout": Layout,
    "ObserverEvent": ObserverEvent,
    "Provenance": Provenance,
    "Runner": Runner,
    "Trace": Trace,
    "TraceRecorder": TraceRecorder,
}


def test_top_level_exports() -> None:
    assert set(simulatte.__all__) == set(EXPECTED)
    for name, obj in EXPECTED.items():
        assert getattr(simulatte, name) is obj, name


def test_component_classes_stay_in_their_modules() -> None:
    for name in ("Server", "ShopFloor", "ProductionJob", "PreShopPool", "Router", "FleetCoordinator"):
        assert name not in simulatte.__all__
        assert not hasattr(simulatte, name)


def test_importing_a_submodule_first_does_not_cycle() -> None:
    import simulatte.intralogistics

    # The intralogistics package imports its names on first use: each of its modules is imported first, then every
    # name of the package is read, in a fresh interpreter.
    folder = Path(simulatte.intralogistics.__file__).parent
    intralogistics = [
        f"simulatte.intralogistics.{path.stem}" for path in sorted(folder.glob("*.py")) if path.stem != "__init__"
    ]
    assert len(intralogistics) > 20
    read = "import simulatte.intralogistics as p; [getattr(p, name) for name in p.__all__]"
    for module in (
        "simulatte.server",
        "simulatte.intralogistics",
        "simulatte.trace",
        "simulatte.logsinks",
        *intralogistics,
    ):
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}; {read}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr


def test_runner_is_lazy_but_discoverable() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, simulatte; assert 'multiprocessing' not in sys.modules; "
            "assert 'tqdm' not in sys.modules; assert 'Runner' in dir(simulatte); "
            "from simulatte import Runner; assert Runner is simulatte.Runner",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_layout_is_lazy_but_discoverable() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, simulatte; "
            "assert 'simulatte.layout' not in sys.modules; "
            "assert 'simulatte.intralogistics.graph' not in sys.modules; "
            "assert 'Layout' in dir(simulatte); from simulatte import Layout; "
            "assert Layout is simulatte.Layout",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_a_layout_without_a_network_imports_no_fleet_traffic_or_agv_module() -> None:
    """Spec §9: preparing, running and reading the layout of a production model leaves these modules unloaded."""
    absent = (
        "simulatte.intralogistics.fleet",
        "simulatte.intralogistics.traffic",
        "simulatte.intralogistics.reservation",
        "simulatte.intralogistics.agv",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from simulatte import Environment, Layout; "
            "from simulatte.builders import build_immediate_release_system; "
            "env = Environment(seed=1); layout = Layout(env); build_immediate_release_system(env=env); "
            "result = layout.prepare(); env.run(until=50); assert result.bundle['network'] is None; "
            "assert env.manifest().requested['layout'] == result.bundle.manifest(); "
            f"loaded = [name for name in {absent!r} if name in sys.modules]; "
            "assert not loaded, loaded",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_intralogistics_names_are_imported_on_first_use() -> None:
    import importlib

    import simulatte.intralogistics as intralogistics

    assert set(intralogistics.__all__) <= set(dir(intralogistics))
    for name in intralogistics.__all__:
        module = importlib.import_module(f"simulatte.intralogistics.{intralogistics._MODULES[name]}")
        assert getattr(intralogistics, name) is getattr(module, name), name
    assert not hasattr(intralogistics, "Missing")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from simulatte.intralogistics.graph import LayoutGraph; "
            "assert 'simulatte.intralogistics.fleet' not in sys.modules; "
            "from simulatte.intralogistics import FleetCoordinator; "
            "from simulatte.intralogistics.fleet import FleetCoordinator as Fleet; assert FleetCoordinator is Fleet",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
