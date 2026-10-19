"""Job populations use declared boundaries without altering the simulated trajectory."""

import importlib
import importlib.util
import pytest
from simulatte import Environment, TraceRecorder
from simulatte.analysis import Population
from simulatte.job import ProductionJob
from simulatte.shopfloor import ShopFloor
from simulatte.server import Server


def api():
    assert importlib.util.find_spec("simulatte.analysis.builtins"), "population collectors missing"
    return importlib.import_module("simulatte.analysis.builtins")


def run_jobs(tmp_path, *, mode="numerical", membership_at="completion", warmup=0):
    b = api()
    env = Environment(seed=7)
    env.configure_kpis(warmup=warmup)
    env.enable_digest()
    floor = ShopFloor(env=env, default_metrics=False)
    server = Server(env=env, capacity=1, shopfloor=floor)
    from simulatte.collectors import ShopFloorKPIs

    ShopFloorKPIs(floor).attach(env)
    collector = None
    if mode != "baseline":
        collector = b.JobOutcomeCollector(
            fields={"family": ("string", lambda job: job.sku)},
            populations=(
                Population("A", lambda row: row["family"] == "A", membership_at, fields=("family",)),
                Population("all", lambda row: True, membership_at),
                Population("empty", lambda row: False, membership_at),
            ),
            membership_at=membership_at,
        ).attach(env)
    if mode == "full":
        TraceRecorder(env, tmp_path / "jobs.simtrace")
    first = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2], due_date=1)
    second = ProductionJob(env=env, sku="A", servers=[server], processing_times=[10], due_date=3)
    floor.add(first)
    floor.add(second)
    first.sku = "B"

    def draws():
        for _ in range(3):
            env.rng("model").random()
            yield env.timeout(1)

    env.process(draws())
    env.run(until=5)
    env.close()
    return collector, (env.fingerprint(), env.rng("model").getstate(), first.finished_at, second.finished_at)


def test_membership_uses_declared_observation_time(tmp_path):
    arrival, _ = run_jobs(tmp_path, membership_at="arrival")
    completion, _ = run_jobs(tmp_path, membership_at="completion")
    assert arrival.rows[0]["family"] == "A"
    assert completion.rows[0]["family"] == "B"
    assert arrival.rows[0]["populations"] == ("A", "all")
    assert completion.rows[0]["populations"] == ("all",)
    assert arrival.rows[0]["lateness"] == 1.0
    assert arrival.rows[0]["completion"] == 2.0
    assert arrival.rows[1]["censored"] and arrival.rows[1]["lateness"] is None
    assert completion.rows[1]["family"] is None and completion.rows[1]["populations"] == ()


def test_warmup_and_invariance(tmp_path):
    baseline = run_jobs(tmp_path, mode="baseline")[1]
    numerical, result = run_jobs(tmp_path)
    full, recorded = run_jobs(tmp_path, mode="full")
    assert baseline == result == recorded
    assert numerical.rows == full.rows
    warm, _ = run_jobs(tmp_path, warmup=3)
    assert [row["included"] for row in warm.rows] == [False, False]


def test_posthoc_population_requires_retained_fields(tmp_path):
    b = api()
    collector, _ = run_jobs(tmp_path, membership_at="arrival")
    result = b.select_population(
        collector.rows,
        Population(
            "late_A",
            lambda row: row["family"] == "A" and row["lateness"] is not None and row["lateness"] > 0,
            "analysis",
            fields=("family", "lateness"),
        ),
    )
    assert result.status == "available" and len(result.rows) == 1
    absent = b.select_population(
        collector.rows, Population("historical", lambda row: row["weight"] > 2, "analysis", fields=("weight",))
    )
    assert absent.status == "insufficient" and absent.missing_fields == ("weight",)


def test_collector_callback_failure_isolated_and_invalid_declarations():
    b = api()
    with pytest.raises(ValueError):
        b.JobOutcomeCollector(fields={}, populations=(), membership_at="later")
    with pytest.raises(ValueError):
        b.JobOutcomeCollector(fields={}, populations=(Population("wrong", bool, "arrival"),))
    with Environment(seed=1) as env:
        collector = b.JobOutcomeCollector(
            fields={"missing": ("string", lambda job: job.no_such_field)}, populations=(), membership_at="arrival"
        ).attach(env)
        floor = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=floor)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=1)
        floor.add(job)
        env.run(until=2)
        assert job.finished_at == 1 and "AttributeError" in collector.failure


@pytest.mark.parametrize("timing", ["arrival", "completion"])
def test_build_then_attach_before_activation_preserves_available_observations(timing):
    b = api()
    with Environment(seed=7) as env:
        floor = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=floor)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=2)
        floor.add(job)
        job.sku = "B"
        collector = b.JobOutcomeCollector(
            fields={"family": ("string", lambda item: item.sku)},
            populations=(Population("B", lambda row: row["family"] == "B", timing, fields=("family",)),),
            membership_at=timing,
        ).attach(env)
        env.run(until=2)
    assert collector.failure is None and len(collector.rows) == 1
    row = collector.rows[0]
    assert row["completion"] == 1 and row["lateness"] == -1
    if timing == "completion":
        assert row["family"] == "B" and row["populations"] == ("B",)
        assert row["membership_available"]
    else:
        assert row["family"] is None and row["populations"] == ()
        assert not row["membership_available"]
        assert "arrival" in row["missing_reason"]


def test_attaching_after_completed_history_reports_limitation():
    b = api()
    with Environment(seed=7) as env:
        floor = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=floor)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=2)
        floor.add(job)
        env.run(until=2)
        collector = b.JobOutcomeCollector(fields={}, populations=()).attach(env)
    assert collector.rows == ()
    assert collector.limitations == (
        "1 jobs completed before collector attachment; completion observations unavailable",
    )


def test_selection_and_population_declarations_reject_invalid_predicates():
    b = api()
    with pytest.raises(ValueError, match="must return bool"):
        b.select_population([{"x": 1}], Population("not_bool", lambda row: 1, "analysis", fields=("x",)))
    twice = (Population("same", lambda row: True, "completion"),) * 2
    with pytest.raises(ValueError, match="Duplicate populations"):
        b.JobOutcomeCollector(fields={}, populations=twice)


def test_retained_fields_allow_temporal_selection_when_complete():
    b = api()
    population = Population("positive", lambda row: row["lateness"] > 0, "analysis", fields=("lateness",))
    rows = [{"lateness": 1.0}, {"lateness": -1.0}]
    result = b.select_population(rows, population, retained_fields=("lateness",))
    assert result.status == "available" and [row["lateness"] for row in result.rows] == [1.0]


def test_job_collector_outside_measurement_plan_is_disabled_and_ignores_unadmitted_jobs():
    from simulatte.analysis import MeasurementPlan

    b = api()
    with Environment(seed=1) as env:
        env.configure_measurements(MeasurementPlan("off", ()))
        disabled = b.JobOutcomeCollector(fields={}, populations=()).attach(env)
        assert disabled._disabled and disabled not in env.collectors
    with Environment(seed=1) as env:
        floor = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=floor)
        ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=2)
        collector = b.JobOutcomeCollector(fields={}, populations=()).attach(env)
        assert collector._pending == {} and collector.limitations == ()


def test_job_admitted_through_pool_keeps_its_first_observation():
    from simulatte.psp import PreShopPool

    b = api()
    with Environment(seed=1) as env:
        floor = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=floor)
        psp = PreShopPool(env=env, shopfloor=floor)
        collector = b.JobOutcomeCollector(
            fields={"family": ("string", lambda job: job.sku)}, populations=(), membership_at="arrival"
        ).attach(env)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1], due_date=5)
        psp.add(job)
        assert set(collector._pending) == {job.id}
        job.sku = "B"
        psp.release(job)
        env.run(until=3)
    assert collector.failure is None and len(collector.rows) == 1
    assert collector.rows[0]["family"] == "A" and collector.rows[0]["completion"] == 1


def test_finish_failure_is_recorded_without_rows():
    from simulatte.analysis.types import Observation

    b = api()
    with Environment(seed=1) as env:
        collector = b.JobOutcomeCollector(fields={}, populations=()).attach(env)
        collector._pending["job-unknown"] = Observation({"job_id": "job-unknown"})
    assert "KeyError" in collector.failure and collector.rows == ()
