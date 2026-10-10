"""The stable entry points exported by the top-level package (spec section 3, D48)."""

from __future__ import annotations

import subprocess
import sys

import simulatte
from simulatte.environment import Environment
from simulatte.events import DomainEvent, Event, ObserverEvent
from simulatte.kpi import KPI, Collector
from simulatte.provenance import Provenance
from simulatte.runner import Runner
from simulatte.trace import Trace, TraceRecorder

EXPECTED = {
    "Collector": Collector,
    "DomainEvent": DomainEvent,
    "Environment": Environment,
    "Event": Event,
    "KPI": KPI,
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
    for module in ("simulatte.server", "simulatte.intralogistics", "simulatte.trace", "simulatte.logsinks"):
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, check=False, timeout=120
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
