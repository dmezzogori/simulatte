"""Observer invariance and determinism of the production builders (spec §13, §17).

Each of ``build_immediate_release_system``, ``build_lumscor_system`` and ``build_slar_system`` runs a short horizon
under five observer configurations, from nothing to everything:

- ``none``: no subscriber of domain events (the shop floor's default metrics are off, logging wants no domain
  event), so nothing is built at an emitting site;
- ``default``: ``Environment()`` defaults (text and history sinks, which take log events only), the shop floor's
  default EMA metrics and the semantic digest;
- ``kpi``: a ``level="kpi"`` trace recorder plus ``ShopFloorKPIs``;
- ``full``: a ``level="full"`` trace recorder plus ``ShopFloorKPIs``;
- ``everything``: a full trace in small chunks, ``ShopFloorTimeSeries``, ``CurrentWorkloadCollector``, a
  ``ServerTimeSeries`` per server, ``ShopFloorKPIs``, ``DEBUG`` logging to a file and ``debug=True`` validation.

The canonical final state compared across all five is :func:`final_state`: ``env.now``, the snapshot of every live
entity (jobs, servers, shop floor, pool, router; observers are not entities), the per-server worked time, the
completed jobs in completion order with their finish times, and the state of every RNG stream, so an observer
that drew random numbers fails too. It reads only the model. Among the four instrumented configurations the
digests are identical, and so are the default EMA metrics and every KPI scalar present in more than one of them
(exact equality). Full
traces verify. Fresh processes with different ``PYTHONHASHSEED`` values reproduce the digest. Finally a counting,
stateful priority policy shows that recording changes neither the number of policy calls nor the schedule, and
that building events never calls user code (spec §6.1, S9).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from simulatte.builders import build_immediate_release_system, build_lumscor_system, build_slar_system
from simulatte.collectors import CurrentWorkloadCollector, ServerTimeSeries, ShopFloorKPIs, ShopFloorTimeSeries
from simulatte.environment import Environment
from simulatte.scenario import Scenario
from simulatte.events import DomainEvent
from simulatte.trace import ChunkLimits, Trace, TraceRecorder
from simulatte.typing import BuiltSystem

SEED = 20261009
HORIZON = 250.0
N_SERVERS = 4
CONFIGURATIONS = ("none", "default", "kpi", "full", "everything")
INSTRUMENTED = ("default", "kpi", "full", "everything")


def build_immediate(env: Environment, **kwargs: Any) -> BuiltSystem[Any]:
    return build_immediate_release_system(env=env, scenario=Scenario(n_servers=N_SERVERS), **kwargs)


def build_lumscor(env: Environment, **kwargs: Any) -> BuiltSystem[Any]:
    return build_lumscor_system(
        env=env,
        scenario=Scenario(n_servers=N_SERVERS),
        check_timeout=5.0,
        wl_norm_level=6.0,
        allowance_factor=2,
        **kwargs,
    )


def build_slar(env: Environment, **kwargs: Any) -> BuiltSystem[Any]:
    return build_slar_system(env=env, scenario=Scenario(n_servers=N_SERVERS), allowance_factor=3.0, **kwargs)


BUILDERS: dict[str, Callable[..., BuiltSystem[Any]]] = {
    "immediate": build_immediate,
    "lumscor": build_lumscor,
    "slar": build_slar,
}


class CountingEnvironment(Environment):
    processed_steps = 0

    def step(self) -> None:
        self.processed_steps += 1
        super().step()


@dataclass
class Outcome:
    state: dict[str, Any]
    digest: str | None
    kpis: dict[str, float]
    ema: dict[str, float]
    trace_path: Path | None


def final_state(env: CountingEnvironment, system: BuiltSystem[Any]) -> dict[str, Any]:
    """The model's final state, read without any observer (see the module docstring)."""
    shop_floor = system.shop_floor
    return {
        "now": env.now,
        "steps": env.processed_steps,
        "entities": env.entities.snapshot(),
        "worked_time": {server.id: server.worked_time for server in system.servers},
        "jobs_done": [(job.id, job.finished_at) for job in shop_floor.jobs_done],
        "jobs_in_system": sorted(job.id for job in shop_floor.jobs),
        "rng": {name: stream.getstate() for name, stream in sorted(env._streams.items())},
    }


def run_configuration(
    builder: str, configuration: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, horizon: float = HORIZON
) -> Outcome:
    """Build the system `builder` under `configuration`, run it and return what the comparisons need."""
    build = BUILDERS[builder]
    trace_path = tmp_path / f"{builder}-{configuration}.simtrace"
    recorded = configuration in ("kpi", "full", "everything")
    if configuration == "everything":
        env = CountingEnvironment(
            seed=SEED, log_level="DEBUG", log_file=tmp_path / f"{builder}.log", debug=True, log_history_size=10
        )
        TraceRecorder(env, trace_path, chunk_limits=ChunkLimits(max_events=200))
    else:
        env = CountingEnvironment(seed=SEED)
        if configuration == "kpi":
            TraceRecorder(env, trace_path, level="kpi")
        elif configuration == "full":
            TraceRecorder(env, trace_path)
        elif configuration == "default":
            env.enable_digest()
    system = build(env, default_metrics=configuration != "none")
    shop_floor = system.shop_floor
    if configuration == "none":
        assert shop_floor.metrics is None
        assert not any(
            issubclass(cls, DomainEvent) or issubclass(DomainEvent, cls)
            for subscription in env.bus._subscriptions
            for cls in subscription._classes
        )
    if configuration in ("kpi", "full", "everything"):
        ShopFloorKPIs(shop_floor).attach(env)
    if configuration == "everything":
        ShopFloorTimeSeries(shop_floor).attach(env)
        CurrentWorkloadCollector(shop_floor).attach(env)
        for server in system.servers:
            ServerTimeSeries(server).attach(env)
    env.run(until=horizon)
    state = final_state(env, system)
    env.close()
    fingerprint = env.fingerprint()
    assert (fingerprint.digest is not None) == (configuration != "none")
    metrics = shop_floor.metrics
    ema = {} if metrics is None else {name: value for name, value in vars(metrics).items() if name.startswith("ema_")}
    return Outcome(state, fingerprint.digest, fingerprint.kpis, ema, trace_path if recorded else None)


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], Outcome]:
    """Every (builder, configuration) run once; the tests below compare them."""
    results: dict[tuple[str, str], Outcome] = {}
    for builder in BUILDERS:
        for configuration in CONFIGURATIONS:
            with pytest.MonkeyPatch.context() as monkeypatch:
                results[builder, configuration] = run_configuration(
                    builder, configuration, tmp_path_factory.mktemp(f"{builder}-{configuration}"), monkeypatch
                )
    return results


@pytest.mark.parametrize("builder", list(BUILDERS))
def test_final_state_is_independent_of_observers(builder: str, outcomes: dict[tuple[str, str], Outcome]) -> None:
    reference = outcomes[builder, "none"].state
    assert reference["jobs_done"], "the horizon must complete jobs"
    assert reference["jobs_in_system"], "the horizon must end with jobs in the system"
    assert any(worked > 0 for worked in reference["worked_time"].values())
    for configuration in INSTRUMENTED:
        assert outcomes[builder, configuration].state == reference, configuration


@pytest.mark.parametrize("builder", list(BUILDERS))
def test_digest_and_shared_kpis_are_identical_across_instrumented_configurations(
    builder: str, outcomes: dict[tuple[str, str], Outcome]
) -> None:
    digests = {configuration: outcomes[builder, configuration].digest for configuration in INSTRUMENTED}
    assert len(set(digests.values())) == 1, digests
    assert next(iter(digests.values())) is not None

    everything = outcomes[builder, "everything"].kpis
    assert any(key.endswith("/throughput") for key in everything)  # ShopFloorKPIs is in the richest configuration
    assert any(key.endswith("/utilization") for key in everything)
    for configuration in ("kpi", "full"):
        assert outcomes[builder, configuration].kpis == everything, configuration
    assert outcomes[builder, "default"].kpis.keys() <= everything.keys()  # no scalar collector by default

    # The default EMA metrics: the same values whatever else observes.
    ema = outcomes[builder, "default"].ema
    assert ema and ema["ema_makespan"] > 0
    for configuration in INSTRUMENTED:
        assert outcomes[builder, configuration].ema == ema, configuration


@pytest.mark.parametrize("builder", list(BUILDERS))
def test_recorded_traces_verify(builder: str, outcomes: dict[tuple[str, str], Outcome]) -> None:
    for configuration in ("full", "everything"):
        path = outcomes[builder, configuration].trace_path
        assert path is not None
        trace = Trace.open(path)
        assert trace.outcome == "completed"
        assert trace.verify() is True, configuration
        assert trace.fingerprint is not None and trace.fingerprint.digest == outcomes[builder, configuration].digest
    path = outcomes[builder, "kpi"].trace_path
    assert path is not None
    assert Trace.open(path).verify() == "not_verifiable"


_HASH_SEED_SCRIPT = f"""
import json
from simulatte.environment import Environment
from simulatte.scenario import Scenario
from simulatte.builders import build_immediate_release_system, build_lumscor_system, build_slar_system

builders = {{
    "immediate": lambda env, scenario: build_immediate_release_system(env=env, scenario=scenario),
    "lumscor": lambda env, scenario: build_lumscor_system(
        env=env, scenario=scenario, check_timeout=5.0, wl_norm_level=6.0, allowance_factor=2
    ),
    "slar": lambda env, scenario: build_slar_system(env=env, scenario=scenario, allowance_factor=3.0),
}}
digests = {{}}
for name, build in builders.items():
    env = Environment(seed={SEED})
    env.enable_digest()
    build(env, Scenario(n_servers={N_SERVERS}))
    env.run(until={HORIZON})
    digests[name] = env.fingerprint().digest
print(json.dumps(digests))
"""


def test_digests_are_stable_across_hash_seeds_in_fresh_processes(outcomes: dict[tuple[str, str], Outcome]) -> None:
    expected = {builder: outcomes[builder, "default"].digest for builder in BUILDERS}
    for hash_seed in ("0", "1", "123"):
        completed = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_SCRIPT],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == expected, hash_seed


# ---------------------------------------------------------------------------------------------------------
# S9: event construction calls no user code
# ---------------------------------------------------------------------------------------------------------


class Tally:
    """Every call a counting policy and its priority values receive."""

    def __init__(self) -> None:
        self.policy_calls = 0
        self.comparisons = 0
        self.presentations = 0  # repr, str, format or float conversion: what recording a value could call


class Rank:
    """A priority that is not a wire value: ordered through counted comparisons, never convertible."""

    def __init__(self, value: float, tally: Tally) -> None:
        self.value = value
        self.tally = tally

    def __lt__(self, other: Rank) -> bool:
        self.tally.comparisons += 1
        return self.value < other.value

    def __eq__(self, other: object) -> bool:
        self.tally.comparisons += 1
        return isinstance(other, Rank) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        self.tally.presentations += 1
        return f"Rank({self.value})"

    def __str__(self) -> str:
        self.tally.presentations += 1
        return repr(self)

    def __format__(self, spec: str) -> str:
        self.tally.presentations += 1
        return repr(self)

    def __float__(self) -> float:
        self.tally.presentations += 1
        return self.value

    def __int__(self) -> int:
        self.tally.presentations += 1
        return int(self.value)


def counting_policy(tally: Tally, *, opaque: bool, offset: int = 0) -> Callable[..., Any]:
    """A stateful priority policy: the value depends on how many times it was called before."""

    def policy(job: Any, server: Any) -> Any:
        tally.policy_calls += 1
        value = job.due_date + 0.5 * ((tally.policy_calls + offset) % 5)
        return Rank(value, tally) if opaque else value

    return policy


def run_with_policy(
    tmp_path: Path, observation: str, *, opaque: bool, offset: int = 0
) -> tuple[Tally, list[tuple[str, float | None]], str | None]:
    tally = Tally()
    env = CountingEnvironment(seed=SEED)
    if observation == "digest":
        env.enable_digest()
    elif observation == "full":
        TraceRecorder(env, tmp_path / "policy.simtrace", chunk_limits=ChunkLimits(max_events=200))
    system = build_immediate_release_system(
        env=env,
        scenario=Scenario(n_servers=N_SERVERS),
        priority_policies=counting_policy(tally, opaque=opaque, offset=offset),
    )
    env.run(until=HORIZON)
    env.close()
    schedule = [(job.id, job.finished_at) for job in system.shop_floor.jobs_done]
    return tally, schedule, env.fingerprint().digest


@pytest.mark.parametrize("opaque", [False, True], ids=["float", "opaque"])
def test_recording_leaves_policy_calls_and_schedule_unchanged(tmp_path: Path, opaque: bool) -> None:
    bare, bare_schedule, no_digest = run_with_policy(tmp_path, "none", opaque=opaque)
    digested, digested_schedule, digest = run_with_policy(tmp_path, "digest", opaque=opaque)
    recorded, recorded_schedule, recorded_digest = run_with_policy(tmp_path, "full", opaque=opaque)

    assert no_digest is None
    assert bare.policy_calls > 1000 and len(bare_schedule) > 20
    assert (digested.policy_calls, recorded.policy_calls) == (bare.policy_calls, bare.policy_calls)
    assert digested_schedule == bare_schedule
    assert recorded_schedule == bare_schedule
    assert digest is not None and recorded_digest == digest
    assert Trace.open(tmp_path / "policy.simtrace").verify() is True
    if opaque:
        # Ordering is the only thing done with the values; comparisons are identical, nothing is rendered.
        assert (digested.comparisons, recorded.comparisons) == (bare.comparisons, bare.comparisons)
        assert bare.comparisons > 1000
        assert (bare.presentations, digested.presentations, recorded.presentations) == (0, 0, 0)

    # The check is sensitive: a policy whose state starts elsewhere schedules differently.
    _, shifted_schedule, _ = run_with_policy(tmp_path, "none", opaque=opaque, offset=1)
    assert shifted_schedule != bare_schedule
