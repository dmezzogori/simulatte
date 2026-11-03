"""Fixed source, separate reporting and control, and independent warmup checks."""

import gzip
import json
from types import SimpleNamespace

import pytest

from simulatte import Environment
from simulatte.experimental.native import Job, NativeProduction
from simulatte.stopping import Horizon, execute_until
from prototypes.native_fixed_admission import plant
from prototypes.native_fixed_admission.analysis import validate_controls, validate_reports


def simulate(path, engine, *, horizon=96, warmup=10, seed=100, **options):
    path.mkdir()
    params = plant.Params(engine=engine, **options)
    with Environment(seed=seed, artifacts_dir=path) as env:
        policy = Horizon(horizon, warmup=warmup)
        env._execution_policy = policy
        env.configure_kpis(warmup=warmup)
        env.enable_digest()
        plant.build(env, None, params)
        execute_until(env, policy)
        fingerprint = env.fingerprint()
    control = json.loads((path / "control.json").read_text())
    reporting = json.loads((path / "reporting.json").read_text())
    raw = {name: (path / (name + ".jsonl.gz")).read_bytes() for name in ("controls", "reports")}
    with gzip.open(path / "controls.jsonl.gz", "rt") as file:
        controls = [json.loads(line) for line in file]
    with gzip.open(path / "reports.jsonl.gz", "rt") as file:
        reports = [json.loads(line) for line in file]
    validate_controls(controls, control, vars(params), seed)
    validate_reports(reports, reporting)
    return dict(control=control, reporting=reporting, raw=raw, fingerprint=fingerprint)


@pytest.mark.parametrize("policy", ["fifo", "spt"])
@pytest.mark.parametrize("seed", [100, 101])
@pytest.mark.parametrize(
    "horizon,hard,cap,windows",
    [(0.25, 12, 4, (0,)), (1, 12, 4, (0,)), (33.25, 8, 2, (0, 10)), (96, 12, 4, (0, 10, 40))],
)
def test_every_engine_and_reporting_window_has_same_inputs(tmp_path, policy, seed, horizon, hard, cap, windows):
    output = []
    for engine in ("python", "full", "bounded"):
        values = [
            simulate(
                tmp_path / f"{engine}-{warmup}",
                engine,
                seed=seed,
                policy=policy,
                horizon=horizon,
                warmup=warmup,
                hard_limit=hard,
                wip_cap=cap,
            )
            for warmup in windows
        ]
        assert all(
            v["control"] == values[0]["control"] and v["raw"]["controls"] == values[0]["raw"]["controls"]
            for v in values
        )
        if engine == "python":
            assert len({v["fingerprint"].digest for v in values}) == 1
        if horizon == 96:
            assert len({json.dumps(v["reporting"]["kpis"], sort_keys=True) for v in values}) > 1
            state = values[0]["control"]["controller"]
            assert all(state[k] > 0 for k in ("switches", "tight_polls", "relaxed_polls", "blocked", "idle"))
        output.append(values)
    for index in range(len(windows)):
        for values in output[1:]:
            assert values[index]["control"] == output[0][index]["control"]
            assert values[index]["raw"] == output[0][index]["raw"]
            assert values[index]["reporting"] == output[0][index]["reporting"]
            assert values[index]["fingerprint"].kpis == output[0][index]["fingerprint"].kpis


def test_requirements_and_absolute_deadline_do_not_move_with_admission():
    spec = plant.specification(41, 17)
    first = Job(spec["offered"] + 1, spec["route"], spec["durations"], spec["due"])
    second = Job(spec["offered"] + 20, spec["route"], spec["durations"], spec["due"])
    assert (first.route, first.durations, first.due) == (second.route, second.durations, second.due)
    assert first.arrival != second.arrival and second.due < second.arrival
    assert spec == plant.specification(41, 17)


def test_feedback_changes_timing_but_keeps_fixed_specification_prefix(tmp_path):
    a = simulate(tmp_path / "on", "bounded")["control"]["controller"]
    b = simulate(tmp_path / "off", "bounded", feedback=False)["control"]["controller"]
    assert a["cursor"] == b["cursor"] == 72
    assert a["specifications_sha256"] == b["specifications_sha256"]
    assert a["admissions_sha256"] != b["admissions_sha256"]
    assert a["delay_eighths"] != b["delay_eighths"]


def test_control_only_poll_does_not_read_kpis(tmp_path):
    class Adapter:
        def add(self, job):
            return SimpleNamespace(id=0)

        def kpi_values(self):
            raise AssertionError("Reporting value used in control-only poll")

    with Environment(seed=100, artifacts_dir=tmp_path) as env:
        controller = plant.Controller(env, plant.Params(record=False))
        controller.adapter = Adapter()
        controller.poll()
        assert controller.cursor == 1


@pytest.mark.parametrize("engine", ["python", "full", "bounded"])
def test_poisoned_reporting_values_cannot_change_control(tmp_path, monkeypatch, engine):
    a = simulate(tmp_path / "plain", engine)
    monkeypatch.setattr(
        plant.Controller,
        "report_values",
        lambda self: {
            name: 1000000.0
            for name in (
                "makespan",
                "lateness",
                "tardiness",
                "tardy_fraction",
                "total_queue_time",
                "throughput",
                "utilization",
                "jobs_in_system",
            )
        },
    )
    b = simulate(tmp_path / "poisoned", engine)
    assert a["control"] == b["control"] and a["raw"]["controls"] == b["raw"]["controls"]
    assert a["fingerprint"] == b["fingerprint"]
    assert a["reporting"] != b["reporting"]


def test_callback_commits_controller_memory_and_schedule(tmp_path):
    with Environment(seed=100, artifacts_dir=tmp_path) as env:
        env._execution_policy = Horizon(96)
        controller = plant.Controller(env, plant.Params(record=False))
        controller.adapter = NativeProduction(3, retention="bounded", input_state=controller.state)
        env.attach_execution_backend(controller.adapter)
        env.activate()
        baseline = controller.state()
        digest = controller.adapter.state_digest()
        for field in ("seed", "pressure_q", "next_at", "tight_mode"):
            value = getattr(controller, field)
            setattr(controller, field, not value if type(value) is bool else value + 1)
            assert controller.adapter.state_digest() != digest
            setattr(controller, field, value)
            assert controller.adapter.state_digest() == digest and controller.state() == baseline


@pytest.mark.parametrize("engine", ["full", "bounded"])
def test_extra_queries_are_pure(tmp_path, monkeypatch, engine):
    a = simulate(tmp_path / "plain", engine)
    monkeypatch.setenv("SIMULATTE_FIXED_EXTRA_QUERIES", "1")
    b = simulate(tmp_path / "queried", engine)
    assert a == b
