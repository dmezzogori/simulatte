"""Research layers preserve authority and distinguish past from future state."""

import copy
import gzip
import json
from pathlib import Path

import pytest

from simulatte import Environment
from simulatte.experimental.native import Job, NativeProduction
from simulatte.stopping import Horizon, execute_until
from prototypes.native_identity_contract import plant, projection as p


def simulate(path, fixture="fixed", engine="bounded", warmup=10):
    path.mkdir()
    with Environment(seed=100, artifacts_dir=path) as env:
        policy = Horizon(96 if fixture == "fixed" else 80, warmup=warmup)
        env._execution_policy = policy
        env.configure_kpis(warmup=warmup)
        env.enable_digest()
        plant.build(env, None, plant.Params(fixture=fixture, engine=engine))
        execute_until(env, policy)
        fingerprint = env.fingerprint()
    return fingerprint, {f.name: f.read_bytes() for f in path.iterdir() if f.is_file()}


@pytest.mark.parametrize("fixture", ["fixed", "coupled"])
@pytest.mark.parametrize("engine", ["python", "full", "bounded"])
def test_instrumentation_preserves_original_bytes(tmp_path, monkeypatch, fixture, engine):
    a, files = simulate(tmp_path / "queried", fixture, engine)
    monkeypatch.setenv("SIMULATTE_IDENTITIES_DISABLE", "1")
    b, baseline = simulate(tmp_path / "baseline", fixture, engine)
    identities = json.loads(files.pop("identities.json"))
    assert a == b and files == baseline
    if engine != "python":
        p.validate(identities["initial"])
        p.validate(identities["final"])
        assert identities["actions"] == identities["final"]["evidence"]["trajectory"]["actions"]
        assert identities["final"]["authority"] == json.loads(files["native.json"])


@pytest.mark.parametrize("fixture", ["fixed", "coupled"])
@pytest.mark.parametrize("engine", ["full", "bounded"])
def test_repeated_queries_are_pure(tmp_path, monkeypatch, fixture, engine):
    a = simulate(tmp_path / "sparse", fixture, engine)
    monkeypatch.setenv("SIMULATTE_IDENTITIES_QUERY_EVERY", "1")
    b = simulate(tmp_path / "dense", fixture, engine)
    assert a == b


@pytest.mark.parametrize("fixture", ["fixed", "coupled"])
@pytest.mark.parametrize("engine", ["full", "bounded"])
def test_reporting_windows_and_realized_trajectory(tmp_path, fixture, engine):
    output = [simulate(tmp_path / str(w), fixture, engine, w) for w in (0, 10, 40)]
    identities = [json.loads(files["identities.json"]) for _, files in output]
    assert len({p.digest(v["initial"]["trajectory_control"]) for v in identities}) == 1
    for phase in ("initial", "final"):
        assert len({p.digest(v[phase]["reporting"]) for v in identities}) == 3
        assert len({p.digest(v[phase]["reproduction_state"]) for v in identities}) == 3
        with pytest.raises(ValueError, match="Complete reproduction"):
            p.require_reproduction(identities[0][phase], identities[1][phase])
    if fixture == "fixed":
        assert len({p.digest(v["final"]["trajectory_control"]) for v in identities}) == 1
    else:
        assert identities[0]["actions"] != identities[1]["actions"]
        assert identities[0]["final"]["trajectory_control"] != identities[1]["final"]["trajectory_control"]


def setup(env, mode="bounded", inputs=None, warmup=0, activate=True):
    env._execution_policy = Horizon(10, warmup=warmup)
    env.configure_kpis(warmup=warmup)
    backend = NativeProduction(1, retention=mode, input_state=inputs)
    env.attach_execution_backend(backend, scope="shopfloor")
    if activate:
        env.activate()
    return backend


@pytest.mark.parametrize("mode", ["full", "bounded"])
def test_unconsumed_future_state_cannot_authorize_reproduction(mode):
    state = dict(seed=1, pressure_q=0, next_at=0.0)
    with Environment() as env:
        backend = setup(env, mode, lambda: state)
        first, _ = p.capture(backend, lambda: state, p.Actions().state())
        for key in state:
            state[key] += 1
            changed, _ = p.capture(backend, lambda: state, p.Actions().state())
            assert changed["trajectory_control"] == first["trajectory_control"]
            assert changed["reporting"] == first["reporting"]
            with pytest.raises(ValueError, match="Complete reproduction"):
                p.require_reproduction(first, changed)
            state[key] -= 1
        again, _ = p.capture(backend, lambda: state, p.Actions().state())
        assert first == again
        p.require_reproduction(first, again)


@pytest.mark.parametrize("mode", ["full", "bounded"])
def test_equal_reporting_snapshot_does_not_erase_completed_history(mode):
    views = []
    for due in (2.0, 3.0):
        with Environment() as env:
            backend = setup(env, mode, inputs=lambda: {}, warmup=5, activate=False)
            env.on_activate(lambda: backend.add(Job(0.0, (0,), (1.0,), due)))
            plant.fixed_case.wake(
                env,
                10,
                priority=-1,
                callback=lambda: views.append(p.capture(backend, lambda: {}, p.Actions().state())[0]),
            )
            execute_until(env, env._execution_policy)
    assert views[0]["reporting"] == views[1]["reporting"]
    assert views[0]["trajectory_control"] != views[1]["trajectory_control"]
    assert views[0]["reproduction_state"] != views[1]["reproduction_state"]


def test_codec_shapes_and_authority_fail_closed():
    with Environment() as env:
        backend = setup(env)
        raw, snapshot = backend._stream.state_bytes(), backend._stream.snapshot()
        p.native_material(raw, snapshot)
        bad = [b"NRST0002" + raw[8:], raw[:40], raw[:-1], raw + b"x"]
        wrong_length = bytearray(raw)
        wrong_length[40:48] = (2**63).to_bytes(8, "little")
        bad.append(bytes(wrong_length))
        wrong_body = bytearray(raw)
        wrong_body[48:56] = b"NBST0002"
        bad.append(bytes(wrong_body))
        for value in bad:
            with pytest.raises(ValueError):
                p.native_material(value, snapshot)
        view, _ = p.capture(backend, None, p.Actions().state())
        with pytest.raises(ValueError, match="authority"):
            p.assemble(raw, snapshot, None, dict(schema="wrong", digest="0" * 64), {}, {}, [], p.Actions().state())
    for field in ("trajectory_control", "reporting", "reproduction_state", "authority"):
        bad = copy.deepcopy(view)
        bad[field]["digest"] = "0" * 64
        with pytest.raises(ValueError):
            p.validate(bad)
    bad = copy.deepcopy(view)
    bad["schema"] = "native-identity-set-v2"
    with pytest.raises(ValueError):
        p.validate(bad)
    with Environment() as env:
        backend = setup(env, "full")
        raw = backend._stream.state_bytes()
        for value in (raw[:-1], raw + b"x", b"NSTA0002" + raw[8:]):
            with pytest.raises(ValueError):
                p.native_material(value, backend._stream.snapshot())


def test_impure_callback_and_closed_backend_rejected():
    state = dict(counter=0)

    def impure():
        state["counter"] += 1
        return state

    with Environment() as env:
        backend = setup(env, inputs=impure)
        with pytest.raises(ValueError):
            p.capture(backend, impure, p.Actions().state())
    with pytest.raises((RuntimeError, ValueError)):
        p.capture(backend, impure, p.Actions().state())


def test_finalized_backend_rejected():
    with Environment() as env:
        backend = setup(env, activate=False)
        execute_until(env, env._execution_policy)
        with pytest.raises(RuntimeError):
            p.capture(backend, None, p.Actions().state())


def test_copied_fixtures_are_identical():
    root = Path(__file__).resolve().parents[2]
    assert (root / "native_identity_contract/fixed_case.py").read_bytes() == (
        root / "native_fixed_admission/plant.py"
    ).read_bytes()
    assert (root / "native_identity_contract/coupled_case.py").read_bytes() == (
        root / "native_feedback_retention/plant.py"
    ).read_bytes()


def test_action_projection_reconstructs_streamed_jobs(tmp_path):
    for fixture in ("fixed", "coupled"):
        _, files = simulate(tmp_path / fixture, fixture)
        name = "controls" if fixture == "fixed" else "frames"
        actions, cursor = p.Actions(), 0
        for line in gzip.decompress(files[name + ".jsonl.gz"]).splitlines():
            row = json.loads(line)
            jobs = []
            for job in row["jobs"]:
                jobs.append(dict(id=cursor, **{k: job[k] for k in ("arrival", "route", "durations", "due")}))
                cursor += 1
            actions.update(row["t"], row["tick"], row["target"], jobs)
        assert actions.state() == json.loads(files["identities.json"])["actions"]
