"""Observer invariance and determinism of the advanced intralogistics example (spec §13, §17).

The real ``examples/intralogistics_advanced.py`` (16 nodes, 3 warehouses, 5 AGVs, replenishment, an 8-hour shift)
runs through ``runpy`` under five observer configurations. The example builds its fleet with
``default_metrics=False`` and attaches an ``OrderEMACollector`` and a ``FleetTimeSeries`` itself; the test swaps
the environment and those two classes for configured versions:

- ``none``: no subscriber of domain events (the two collectors become inert stand-ins), default logging;
- ``default``: the example's collectors, default logging and the semantic digest;
- ``kpi``: a ``level="kpi"`` trace recorder plus ``FleetKPIs``;
- ``full``: a ``level="full"`` trace recorder plus ``FleetKPIs``;
- ``everything``: a full trace in small chunks, ``FleetKPIs``, ``DEBUG`` logging to a file and ``debug=True``.

The canonical final state compared across all five is :func:`final_state`: ``env.now``, the snapshot of every live
entity (AGVs, warehouses and their inventories, orders, the fleet), every AGV's time allocation and utilization,
the fleet utilization, the order statuses and the state of every RNG stream. It reads only the model. Among the
four instrumented configurations the digests, the printed report, the EMA metrics and every KPI scalar present in
more than one of them are identical (exact equality), full traces verify, and fresh processes with different
``PYTHONHASHSEED`` values reproduce the digest.
"""

from __future__ import annotations

import contextlib
import io
import os
import runpy
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot
import pytest

import simulatte.environment
import simulatte.intralogistics as intralogistics
from simulatte.environment import Environment
from simulatte.intralogistics import AGV, AGVState, FleetCoordinator, FleetKPIs, OrderEMACollector
from simulatte.intralogistics.events import AgvStateChanged, OrderStatusChanged
from simulatte.trace import ChunkLimits, Trace, TraceRecorder

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "intralogistics_advanced.py"
CONFIGURATIONS = ("none", "default", "kpi", "full", "everything")
INSTRUMENTED = ("default", "kpi", "full", "everything")


class Inert:
    """Stand-in for a collector of the example when nothing may subscribe: attaches nothing, plots nothing."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.inventory_ts: dict[str, Any] = {}

    def attach(self, env: Environment) -> Inert:
        return self

    def __getattr__(self, name: str) -> Any:
        if name.startswith("plot_"):
            return lambda *args, **kwargs: None
        if name.startswith("ema_"):
            return None
        raise AttributeError(name)


class OrderEMAWithFleetKPIs(OrderEMACollector):
    """The example's EMA collector, which also attaches ``FleetKPIs`` to the same fleet."""

    def attach(self, env: Environment) -> OrderEMAWithFleetKPIs:
        FleetKPIs(self.scope).attach(env)  # ty: ignore[invalid-argument-type]  # the scope of a fleet collector
        return super().attach(env)


@dataclass
class Outcome:
    state: dict[str, Any]
    digest: str | None
    kpis: dict[str, float]
    report: str
    trace_path: Path | None


def final_state(env: Environment) -> dict[str, Any]:
    """The model's final state, read without any observer (see the module docstring)."""
    agvs = [entity for entity in env.entities.live() if isinstance(entity, AGV)]
    (fleet,) = (entity for entity in env.entities.live() if isinstance(entity, FleetCoordinator))
    return {
        "now": env.now,
        "entities": env.entities.snapshot(),
        "agvs": {
            agv.id: {
                "allocation": {state.name: agv.time_allocation()[state] for state in AGVState},
                "utilization": agv.utilization(),
                "durations": {state.name: agv.state_durations[state] for state in AGVState},
            }
            for agv in agvs
        },
        "fleet_utilization": fleet.fleet_utilization,
        "pending": fleet.pending_count,
        "rng": {name: stream.getstate() for name, stream in sorted(env._streams.items())},
    }


def run_example(configuration: str, tmp_path: Path) -> Outcome:
    """Run the advanced example under `configuration` and return what the comparisons need."""
    trace_path = tmp_path / f"fleet-{configuration}.simtrace"
    created: list[Environment] = []

    def make_environment(**kwargs: Any) -> Environment:
        if configuration == "everything":
            env = Environment(**kwargs, log_level="DEBUG", log_file=tmp_path / "fleet.log", debug=True)
            TraceRecorder(env, trace_path, chunk_limits=ChunkLimits(max_events=500))
        else:
            env = Environment(**kwargs)
            if configuration == "default":
                env.enable_digest()
            elif configuration == "kpi":
                TraceRecorder(env, trace_path, level="kpi")
            elif configuration == "full":
                TraceRecorder(env, trace_path)
        created.append(env)
        return env

    printed = io.StringIO()
    with pytest.MonkeyPatch.context() as monkeypatch, contextlib.redirect_stdout(printed):
        monkeypatch.setattr(matplotlib.pyplot, "show", lambda *args, **kwargs: None)
        monkeypatch.setattr(simulatte.environment, "Environment", make_environment)
        if configuration == "none":
            monkeypatch.setattr(intralogistics, "OrderEMACollector", Inert)
            monkeypatch.setattr(intralogistics, "FleetTimeSeries", Inert)
        elif configuration in ("kpi", "full", "everything"):
            monkeypatch.setattr(intralogistics, "OrderEMACollector", OrderEMAWithFleetKPIs)
        runpy.run_path(str(EXAMPLE), run_name="__main__")  # closes the environment on leaving its ``with``
    report = printed.getvalue()

    (env,) = created
    if configuration == "none":
        for event_type in (AgvStateChanged, OrderStatusChanged):
            assert not env.wants(event_type)
    fingerprint = env.fingerprint()
    assert (fingerprint.digest is not None) == (configuration != "none")
    recorded = configuration in ("kpi", "full", "everything")
    return Outcome(final_state(env), fingerprint.digest, fingerprint.kpis, report, trace_path if recorded else None)


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Outcome]:
    """Every configuration run once; the tests below compare them."""
    return {
        configuration: run_example(configuration, tmp_path_factory.mktemp(configuration))
        for configuration in CONFIGURATIONS
    }


def test_final_state_is_independent_of_observers(outcomes: dict[str, Outcome]) -> None:
    reference = outcomes["none"].state
    assert reference["now"] == 28800.0
    assert any(agv["allocation"]["CHARGING"] > 0 for agv in reference["agvs"].values())
    assert any(agv["utilization"] > 0.1 for agv in reference["agvs"].values())
    for configuration in INSTRUMENTED:
        assert outcomes[configuration].state == reference, configuration

    # Orders are not entities; their outcome is in the example's report up to the EMA section (counts,
    # fulfillment time, inventories, fleet report), which every configuration prints.
    orders = outcomes["none"].report.split("EMA metrics:")[0]
    assert "Completed: " in orders and "Failed: " in orders
    for configuration in INSTRUMENTED:
        assert outcomes[configuration].report.split("EMA metrics:")[0] == orders, configuration


def test_digest_report_and_shared_kpis_are_identical_across_instrumented_configurations(
    outcomes: dict[str, Outcome],
) -> None:
    digests = {configuration: outcomes[configuration].digest for configuration in INSTRUMENTED}
    assert len(set(digests.values())) == 1, digests
    assert next(iter(digests.values())) is not None

    everything = outcomes["everything"].kpis
    assert any(key.endswith("/throughput") for key in everything)
    assert any(key.endswith("/utilization") for key in everything)
    assert any(key.endswith("/fulfillment_time") for key in everything)
    for configuration in ("kpi", "full"):
        assert outcomes[configuration].kpis == everything, configuration
    assert outcomes["default"].kpis.keys() <= everything.keys()
    shared = outcomes["default"].kpis.keys() & everything.keys()
    assert {key: outcomes["default"].kpis[key] for key in shared} == {key: everything[key] for key in shared}

    # The example's own report (counts, inventories, EMA metrics, fleet utilization) does not depend on the observers.
    report = outcomes["default"].report
    assert "Fleet utilization:" in report and "EMA metrics:" in report
    for configuration in INSTRUMENTED:
        assert outcomes[configuration].report == report, configuration


def test_recorded_traces_verify(outcomes: dict[str, Outcome]) -> None:
    for configuration in ("full", "everything"):
        path = outcomes[configuration].trace_path
        assert path is not None
        trace = Trace.open(path)
        assert trace.outcome == "completed"
        assert trace.verify() is True, configuration
        assert trace.fingerprint is not None and trace.fingerprint.digest == outcomes[configuration].digest
    path = outcomes["kpi"].trace_path
    assert path is not None
    assert Trace.open(path).verify() == "not_verifiable"


_HASH_SEED_SCRIPT = """
import runpy
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot

import simulatte.environment

matplotlib.pyplot.show = lambda *args, **kwargs: None
real_environment = simulatte.environment.Environment
created = []


def make_environment(**kwargs):
    env = real_environment(**kwargs)
    env.enable_digest()
    created.append(env)
    return env


simulatte.environment.Environment = make_environment
runpy.run_path(sys.argv[1], run_name="__main__")
(env,) = created
print("DIGEST", env.fingerprint().digest)
"""


def test_digest_is_stable_across_hash_seeds_in_fresh_processes(outcomes: dict[str, Outcome]) -> None:
    for hash_seed in ("0", "1", "123"):
        completed = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_SCRIPT, str(EXAMPLE)],
            env={**os.environ, "PYTHONHASHSEED": hash_seed, "MPLBACKEND": "Agg"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.splitlines()[-1] == f"DIGEST {outcomes['default'].digest}", hash_seed
