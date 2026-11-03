"""Public-hook composition agrees with both the frozen parser and original model."""

import os
from pathlib import Path

import pytest

from simulatte import Environment
from simulatte.stopping import Horizon, execute_until
from prototypes.native_provider_projection import plant, reference_projection


def simulate(path, fixture, retention):
    path.mkdir()
    with Environment(seed=100, artifacts_dir=path) as env:
        policy = Horizon(96 if fixture == "fixed" else 80, warmup=10)
        env._execution_policy = policy
        env.configure_kpis(warmup=10)
        env.enable_digest()
        plant.build(env, None, plant.Params(fixture=fixture, engine=retention))
        execute_until(env, policy)
        fingerprint = env.fingerprint()
    return fingerprint, {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}


@pytest.mark.parametrize("fixture", ["fixed", "coupled"])
@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_hook_reference_compare_and_disabled_controls(tmp_path, monkeypatch, fixture, retention):
    output = []
    for mode in ("hook", "reference", "compare"):
        monkeypatch.setenv("SIMULATTE_PROJECTION_MODE", mode)
        monkeypatch.setenv("SIMULATTE_IDENTITIES_QUERY_EVERY", "1")
        output.append(simulate(tmp_path / mode, fixture, retention))
    assert output[0] == output[1] == output[2]
    monkeypatch.setenv("SIMULATTE_IDENTITIES_DISABLE", "1")
    base, files = simulate(tmp_path / "disabled", fixture, retention)
    measured, original = output[0]
    original.pop("identities.json")
    assert measured == base and original == files


def test_default_hook_never_calls_binary_parser(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMULATTE_PROJECTION_MODE", raising=False)
    monkeypatch.setenv("SIMULATTE_IDENTITIES_QUERY_EVERY", "1")

    def fail(*args):
        raise AssertionError("Public consumer accessed binary parser")

    monkeypatch.setattr(reference_projection, "capture", fail)
    monkeypatch.setattr(reference_projection, "native_material", fail)
    simulate(tmp_path / "hook", "fixed", "bounded")


def test_frozen_reference_and_original_fixtures_unchanged():
    root = Path(__file__).resolve().parents[2]
    for new, old in (
        ("reference_projection.py", "projection.py"),
        ("fixed_case.py", "fixed_case.py"),
        ("coupled_case.py", "coupled_case.py"),
    ):
        assert (root / "native_provider_projection" / new).read_bytes() == (
            root / "native_identity_contract" / old
        ).read_bytes()
    assert os.environ.get("SIMULATTE_PROJECTION_MODE", "hook") in ("hook", "reference", "compare")
