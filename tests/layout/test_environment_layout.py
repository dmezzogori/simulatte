"""Every environment owns a layout (spec §5.1, §5.5, §5.8 of ``specs/2026-11-23-layout-only-models-design.md``).

``env.layout`` creates the layout on first access, activation creates it when nothing else did, and the trace of a
production-only model carries it.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulatte import Environment, Layout, TraceRecorder
from simulatte.builders import build_immediate_release_system
from simulatte.entities import EntityCreated
from simulatte.events import DomainEvent
from simulatte.intralogistics.builders import build_simple_system
from simulatte.job import ProductionJob
from simulatte.layout import GraphRef, LayoutError, LayoutLimits
from simulatte.layout.lifecycle import PreparationState
from simulatte.server import Server
from simulatte.trace import Trace
from tests.core.test_g1_acceptance import _GOLDEN_PLATFORM, GOLDEN_REFERENCE, HORIZON, SEED, build_reference_shop


def test_a_fresh_environment_has_a_layout_created_on_first_access() -> None:
    env = Environment()
    server = Server(env=env, capacity=1)
    assert env._layout is None
    layout = env.layout
    assert isinstance(layout, Layout) and layout.env is env
    assert env.layout is layout and env.layout is layout
    assert server.id in layout.prepare().values.targets  # creation registers the entities already attached


def test_the_constructor_on_a_fresh_environment_is_its_layout(tmp_path: Path) -> None:
    env = Environment()
    layout = Layout(env)
    assert env.layout is layout
    path = tmp_path / "layout.json"
    path.write_text('{"schema_version": "1.0"}')
    from_file = Environment()
    layout = Layout(from_file, file=path)
    assert from_file.layout is layout
    from_dict = Environment()
    layout = Layout.from_dict(from_dict, {"schema_version": "1.0"})
    assert from_dict.layout is layout


def test_constructor_after_first_use_raises(tmp_path: Path) -> None:
    path = tmp_path / "layout.json"
    path.write_text('{"schema_version": "1.0"}')
    env = Environment()
    first = env.layout
    for create in (lambda: Layout(env, file=path), lambda: Layout.from_dict(env, {"schema_version": "1.0"})):
        with pytest.raises(LayoutError) as raised:
            create()
        message = str(raised.value)
        assert "already has a layout" in message
        assert "Layout(env, ...) must come before the first use of env.layout" in message
        assert "explicit graph consumers" not in message
    assert env.layout is first


def test_activation_creates_the_layout_of_a_production_model() -> None:
    env = Environment(seed=1)
    build_immediate_release_system(env=env)
    assert env._layout is None
    env.activate()
    layout = env._layout
    assert isinstance(layout, Layout) and env.layout is layout
    assert layout._coordinator.state is PreparationState.FINALIZED
    assert {"wc-0", "shopfloor", "router"} <= set(layout.prepare().values.targets)
    assert env.manifest().requested["layout"] == layout.prepare().bundle.manifest()
    ran = Environment(seed=1)
    build_immediate_release_system(env=ran)
    ran.run(until=10)
    assert ran._layout is not None and ran._layout._coordinator.state is PreparationState.FINALIZED


def _reference_values() -> dict:
    env = Environment(seed=SEED)
    env.enable_digest()
    events: list[DomainEvent] = []
    env.bus.subscribe(lambda event: events.append(event) if event.ordinal is not None else None, "*")
    build_reference_shop(env)
    env.run(until=HORIZON)
    assert env._layout is not None  # created at activation
    initial = sorted(env.initial_state)
    created = [event.entity for event in events if isinstance(event, EntityCreated)]
    fingerprint = env.fingerprint()
    return {
        "initial": initial,
        "ids": hashlib.sha256(json.dumps([initial, created]).encode()).hexdigest(),
        "events": len(events),
        "digest": fingerprint.digest,
        "kpis": fingerprint.kpis,
    }


def test_a_production_model_keeps_its_ids_events_and_fingerprint() -> None:
    """The G1 reference shop as at 8957ddb, before activation created a layout (spec §5.5)."""
    values = _reference_values()
    assert values["initial"] == ["lathe", "psp", "router", "shopfloor", "wc-0", "wc-1", "wc-2", "wc-3"]
    assert values == _reference_values()
    if not _GOLDEN_PLATFORM:
        pytest.skip("pinned on macOS arm64 (libm-dependent samples), as the G1 golden values")
    assert values == {
        "initial": ["lathe", "psp", "router", "shopfloor", "wc-0", "wc-1", "wc-2", "wc-3"],
        "ids": "90ddf964735256d22538e13f20fc97b6dcc13bd74adbcc8eb4a894d315973adc",
        "events": 10700,
        "digest": GOLDEN_REFERENCE[sys.implementation.name][0],
        "kpis": {},
    }


def test_the_trace_of_a_production_model_carries_its_layout(tmp_path: Path) -> None:
    path = tmp_path / "reference.simtrace"
    env = Environment(seed=SEED)
    TraceRecorder(env, path)
    build_reference_shop(env)
    env.run(until=50.0)
    env.close()
    trace = Trace.open(path)
    assert trace.layout is not None
    assert trace.layout == env.layout.prepare().bundle
    assert trace._initial_manifest["layout"] == trace.layout.manifest() == env.manifest().requested["layout"]
    bundle = trace.layout.to_dict()
    assert set(bundle["targets"]) >= {"lathe", "psp", "router", "shopfloor", "wc-0"}
    assert set(bundle["resolved"]["placements"]) >= {"lathe", "psp", "wc-0", "wc-3"}
    assert trace.verify() is True


def test_too_many_targets_names_the_limit() -> None:
    env = Environment()
    Layout(env, limits=LayoutLimits(max_placements=20))
    server = Server(env=env, capacity=1)
    for _ in range(30):
        ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    with pytest.raises(LayoutError) as raised:
        env.activate()
    message = str(raised.value)
    assert "max_placements" in message and "20" in message and "31" in message
    assert "Layout(env, limits=...)" in message
    assert not env.activated


def test_step_before_activation_names_activate() -> None:
    env = Environment()
    Server(env=env, capacity=1)
    env.timeout(1)
    with pytest.raises(LayoutError, match=r"env\.activate\(\)"):
        env.step()
    assert env.now == 0 and env._layout is None
    env.activate()
    env.step()
    assert env.now == 1


def test_an_initializer_cannot_step() -> None:
    env = Environment()
    env.timeout(3)
    env.on_activate(env.step)
    with pytest.raises(LayoutError, match=r"env\.activate\(\)"):
        env.activate()
    assert env.now == 0


def test_checks_for_a_layout_create_none() -> None:
    """Library code that asks whether a layout exists reads ``env._layout``: building and manifests leave a fresh
    environment without a layout until activation. The transport builder uses ``env.layout`` (spec §5.7)."""
    env = Environment(seed=1)
    build_immediate_release_system(env=env)
    env.manifest()
    assert env._layout is None
    transport = Environment()
    _, _, _, _, graph = build_simple_system(transport)
    assert isinstance(graph, GraphRef) and graph.owner is transport._layout is transport.layout


def test_a_production_model_and_a_transport_system_share_the_environments_layout() -> None:
    env = Environment(seed=1)
    build_immediate_release_system(env=env)
    _, _, _, _, graph = build_simple_system(env)
    assert graph.owner is env.layout
    env.run(until=10)
    assert env.manifest().requested["layout"] == env.layout.prepare().bundle.manifest()
    assert {"wc-0", "shopfloor", "WH-A", "vehicle-0"} <= set(env.layout.prepare().values.targets)


def test_a_layout_created_at_activation_imports_no_fleet_traffic_or_agv_module() -> None:
    """Spec §9 and L9: ``import simulatte`` and building a production model import no layout module; activating it
    imports the layout package and none of the fleet, traffic, reservation or AGV modules."""
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
            "import sys; from simulatte import Environment; "
            "from simulatte.builders import build_immediate_release_system; "
            "env = Environment(seed=1); build_immediate_release_system(env=env); "
            "assert 'simulatte.layout' not in sys.modules; "
            "assert 'simulatte.intralogistics.graph' not in sys.modules; "
            "env.run(until=50); assert 'simulatte.layout' in sys.modules; "
            "assert env.manifest().requested['layout'] == env.layout.prepare().bundle.manifest(); "
            f"loaded = [name for name in {absent!r} if name in sys.modules]; "
            "assert not loaded, loaded",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
