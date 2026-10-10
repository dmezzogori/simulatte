"""Provenance and the run manifest (spec §9.3)."""

from __future__ import annotations

import json
import platform
from collections.abc import Iterable
from typing import Any

import pytest

from simulatte._wire import FrozenMap
from simulatte.builders import build_immediate_release_system
from simulatte.environment import Environment
from simulatte import provenance as provenance_module
from simulatte.provenance import UNAVAILABLE, Provenance, RunManifest, _installed_distributions
from simulatte.scenario import Scenario

FULL = Provenance(model="m" * 8, source="s" * 8, inputs="i" * 8, dependencies="d" * 8)


def test_provenance_defaults_to_unavailable() -> None:
    assert Provenance() == Provenance(UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE)


def test_manifest_seed_is_decimal_string() -> None:
    seed = 2**63 - 1  # beyond the JavaScript safe integer range
    manifest = Environment(seed=seed, time_unit="minute").manifest()
    requested = manifest.requested
    assert requested["seed"] == "9223372036854775807"
    assert isinstance(requested["seed"], str)
    assert requested["rng_derivation"] == "simulatte-rng-v1"
    assert requested["time_unit"] == "minute"
    assert requested["warmup"] == 0.0
    assert requested["parameters"] == FrozenMap({})
    assert requested["python"] == FrozenMap(
        {"implementation": platform.python_implementation(), "version": platform.python_version()}
    )
    assert isinstance(requested["simulatte_version"], str)
    system = requested["platform"]
    assert isinstance(system, FrozenMap) and set(system) == {"system", "machine"}
    assert Environment(seed=0).manifest().requested["seed"] == "0"
    assert Environment().manifest().requested["time_unit"] is None


def test_manifest_dependencies_come_from_provenance_else_the_installed_listing() -> None:
    given = Environment(seed=1, provenance=FULL).manifest().requested
    assert given["dependencies"] == FrozenMap({"source": "provenance", "hash": "d" * 8})
    assert given["provenance"] == FrozenMap(
        {"model": "m" * 8, "source": "s" * 8, "inputs": "i" * 8, "dependencies": "d" * 8}
    )

    listed = Environment(seed=1).manifest().requested["dependencies"]
    assert isinstance(listed, FrozenMap)
    assert listed["source"] == "installed-distributions"
    packages = listed["packages"]
    assert isinstance(packages, FrozenMap)
    assert "msgpack" in packages and "simpy" in packages
    assert list(packages) == sorted(packages)


def test_manifest_requested_is_fixed_by_activation_and_deterministic() -> None:
    env = Environment(seed=9, provenance=FULL)
    before = env.manifest()
    env.activate()
    after = env.manifest()
    assert before.requested == after.requested
    assert after.requested is env.manifest().requested
    assert before.final is None
    assert Environment(seed=9, provenance=FULL).manifest().requested == before.requested


def test_manifest_complete_rules() -> None:
    def run(env: Environment) -> RunManifest:
        env.run(until=5)
        return env.manifest()

    assert not Environment(seed=1, provenance=FULL).manifest().complete  # no final part yet
    assert run(Environment(seed=1, provenance=FULL)).complete
    assert not run(Environment(seed=1)).complete  # provenance unavailable
    assert not run(Environment(seed=1, provenance=Provenance(model="m", source="s", inputs="i"))).complete

    env = Environment(seed=1, provenance=FULL)
    env.bind(lambda: 1.0, kind="scalar", stream="s", owner="my-owner")
    opaque = run(env)
    assert not opaque.complete
    assert opaque.final is not None
    assert opaque.final["opaque_sampler_owners"] == ("my-owner",)
    assert opaque.final["complete"] is False

    managed = Environment(seed=1, provenance=FULL)
    managed.bind(2.0, kind="scalar", stream="s", owner="managed-owner")
    done = run(managed)
    assert done.complete
    assert done.final is not None and done.final["opaque_sampler_owners"] == ()


def test_run_stopped_by_event_is_incomplete() -> None:
    # The stop cannot be reproduced from the manifest, which records only {"type": "event"} (R11).
    env = Environment(seed=1, provenance=FULL)
    env.run(until=env.timeout(3))
    manifest = env.manifest()
    assert manifest.final is not None
    assert manifest.final["stopping_policy"] == FrozenMap({"type": "event"})
    assert manifest.final["complete"] is False
    assert not manifest.complete
    env.run(until=5)  # the final part follows the last run
    assert env.manifest().complete


def test_manifest_final_records_last_horizon() -> None:
    env = Environment(seed=1)
    build_immediate_release_system(env=env, scenario=Scenario(n_servers=2))
    assert env.manifest().final is None
    env.run(until=10)
    final = env.manifest().final
    assert final is not None and final["stopping_policy"] == FrozenMap({"type": "horizon", "horizon": 10.0})
    env.run(until=25.5)
    final = env.manifest().final
    assert final is not None and final["stopping_policy"] == FrozenMap({"type": "horizon", "horizon": 25.5})

    drained = Environment(seed=1)
    drained.run()
    final = drained.manifest().final
    assert final is not None and final["stopping_policy"] == FrozenMap({"type": "exhaustion"})

    watched = Environment(seed=1)
    watched.run(until=watched.timeout(3))
    final = watched.manifest().final
    assert final is not None and final["stopping_policy"] == FrozenMap({"type": "event"})


def test_merged_manifest_has_both_parts_and_no_volatile_metadata() -> None:
    env = Environment(seed=4, provenance=FULL)
    env.run(until=3)
    manifest = env.manifest()
    merged = manifest.merged()
    assert set(merged) == set(manifest.requested) | set(manifest.final or {})
    assert merged["seed"] == "4" and merged["stopping_policy"] == FrozenMap({"type": "horizon", "horizon": 3.0})
    assert RunManifest(manifest.requested, None).merged() is manifest.requested
    text = json.dumps(sorted(merged))
    assert "host" not in text and "wall" not in text and "seconds" not in text


def test_volatile_metadata_is_separate() -> None:
    env = Environment(seed=1)
    assert env.volatile_metadata().wall_clock_start is None
    env.run(until=2)
    volatile = env.volatile_metadata()
    assert volatile.wall_clock_start is not None
    assert volatile.run_seconds >= 0.0
    assert volatile.host
    assert Environment(seed=1).manifest().requested == env.manifest().requested  # unaffected by the execution


def test_activation_does_not_scan_installed_distributions(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = provenance_module.metadata.distributions

    def counting() -> Iterable[Any]:
        calls.append(1)
        return real()

    _installed_distributions.cache_clear()
    monkeypatch.setattr(provenance_module.metadata, "distributions", counting)
    try:
        env = Environment(seed=1)
        env.activate()
        assert calls == []  # activation captures only cheap values
        first = env.manifest().requested
        assert len(calls) == 1
        assert env.manifest().requested is first
        other = Environment(seed=2)
        other.activate()
        assert other.manifest().requested["dependencies"] == first["dependencies"]
        assert len(calls) == 1  # the listing is cached per process
    finally:
        _installed_distributions.cache_clear()


def test_requested_values_are_fixed_at_activation() -> None:
    env = Environment(seed=1, time_unit="minute")
    env.activate()
    env.time_unit = "hour"
    assert env.manifest().requested["time_unit"] == "minute"
    late = Environment(seed=1, time_unit="minute")
    late.time_unit = "hour"  # before activation the current value is shown
    assert late.manifest().requested["time_unit"] == "hour"
