"""The prepared composition preserves every original model artifact and fingerprint."""

import pytest

from simulatte import Environment
from simulatte.stopping import Horizon, execute_until
from prototypes.native_prepared_identity import plant, projection


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
def test_all_four_consumers_match_at_every_poll(tmp_path, monkeypatch, fixture, retention):
    monkeypatch.setenv("SIMULATTE_IDENTITIES_QUERY_EVERY", "1")
    values = []
    for mode in ("hook", "unprepared", "reference", "compare"):
        monkeypatch.setenv("SIMULATTE_PROJECTION_MODE", mode)
        values.append(simulate(tmp_path / mode, fixture, retention))
    assert all(v == values[0] for v in values[1:])


def test_candidate_uses_public_hook_with_no_unprepared_or_parser_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMULATTE_PROJECTION_MODE", "hook")
    monkeypatch.setenv("SIMULATTE_IDENTITIES_QUERY_EVERY", "1")

    def fail(*args):
        raise AssertionError("Candidate used a frozen implementation")

    monkeypatch.setattr(projection.unprepared, "hook_capture", fail)
    monkeypatch.setattr(projection.reference, "capture", fail)
    monkeypatch.setattr(projection.reference, "native_material", fail)
    simulate(tmp_path / "candidate", "fixed", "bounded")
