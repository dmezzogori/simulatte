"""Interval observations retain classification boundaries and aggregation semantics."""

import importlib
import importlib.util
import pytest
from simulatte import Environment
from simulatte.analysis import Population
from simulatte.intralogistics import AGVState, build_simple_system


def api():
    assert importlib.util.find_spec("simulatte.analysis.builtins"), "population collectors missing"
    return importlib.import_module("simulatte.analysis.builtins")


def test_intervals_clip_and_keep_boundary_classification():
    b = api()
    with Environment(seed=3) as env:
        env.configure_kpis(warmup=1)
        agv = build_simple_system(env, n_agvs=1)[0].fleet[0]
        agv.capabilities = ["lift"]
        collector = b.AgvDurationCollector(
            fields={"capabilities": ({"list": "string"}, lambda a: a.capabilities)},
            populations=(Population("lift", lambda row: "lift" in row["capabilities"], "interval_start"),),
        ).attach(env)

        def work():
            yield env.timeout(2)
            agv.capabilities.append("tow")
            agv.transition_to(AGVState.TRAVELING_LOADED)
            yield env.timeout(2)
            agv.capabilities = ["tow"]
            collector.observe_boundary(agv)

        env.process(work())
        env.run(until=6)
    assert [(r["start"], r["end"], r["duration"]) for r in collector.rows] == [
        (1.0, 2.0, 1.0),
        (2.0, 4.0, 2.0),
        (4.0, 6.0, 2.0),
    ]
    assert [r["capabilities"] for r in collector.rows] == [("lift",), ("lift", "tow"), ("tow",)]
    assert [r["populations"] for r in collector.rows] == [("lift",), ("lift",), ()]


def test_unequal_durations_and_zero_window():
    b = api()
    rows = [
        dict(agv_id="a", state="TRAVELING_LOADED", duration=1.0),
        dict(agv_id="a", state="IDLE", duration=1.0),
        dict(agv_id="b", state="TRAVELING_LOADED", duration=1.0),
        dict(agv_id="b", state="IDLE", duration=7.0),
    ]
    summary = b.fleet_utilization(rows)
    assert summary["mean_of_vehicle_fractions"] == 0.3125
    assert summary["ratio_of_durations"] == 0.2
    assert summary["vehicles"] == 2
    assert b.fleet_utilization([dict(agv_id="a", state="IDLE", duration=0.0)])["ratio_of_durations"] is None
    with Environment(seed=2) as env:
        build_simple_system(env, n_agvs=1)[0].fleet[0]
        collector = b.AgvDurationCollector(fields={}, populations=()).attach(env)
        env.activate()
    assert collector.rows[0]["duration"] == 0 and not collector.rows[0]["included"]


def test_aggregate_only_rejects_temporal_population():
    b = api()
    result = b.select_population(
        (),
        Population("later", lambda row: True, "analysis", fields=("start",)),
        retained_fields=("state", "duration"),
        temporal=False,
    )
    assert result.status == "insufficient" and result.missing_fields == ("start",)
    with pytest.raises(ValueError):
        b.AgvDurationCollector(fields={}, populations=(), membership_at="completion")


def test_fleet_observation_invariance(tmp_path):
    b = api()
    from simulatte import TraceRecorder
    from simulatte.intralogistics import FleetKPIs

    results = []
    for mode in ("baseline", "numerical", "full"):
        with Environment(seed=42) as env:
            fleet, _, origin, destination, _ = build_simple_system(env, n_agvs=2)
            FleetKPIs(fleet).attach(env)
            env.enable_digest()
            if mode != "baseline":
                b.AgvDurationCollector(fields={"family": ("string", lambda a: a.agv_type.name)}, populations=()).attach(
                    env
                )
            if mode == "full":
                TraceRecorder(env, tmp_path / "fleet.simtrace")
            order = fleet.create_order(
                sku=next(iter(origin.inventory)), quantity=1, origin=origin, destination=destination
            )
            fleet.submit(order)
            env.run(until=100)
            results.append((env.fingerprint(), {k: r.getstate() for k, r in env._streams.items()}, order.delivered_at))
    assert results[0] == results[1] == results[2]


def _fleet(env, n_agvs=1):
    return build_simple_system(env, n_agvs=n_agvs)[0].fleet


def test_field_failure_during_activation_is_isolated():
    b = api()
    with Environment(seed=1) as env:
        _fleet(env)
        collector = b.AgvDurationCollector(
            fields={"bad": ("string", lambda agv: agv.no_such_field)}, populations=()
        ).attach(env)
        env.activate()
    assert "AttributeError" in collector.failure and collector.rows == ()


def test_boundary_observation_requires_same_environment_and_live_collector():
    b = api()
    with Environment(seed=1) as env, Environment(seed=2) as other:
        agv = _fleet(env)[0]
        foreign = _fleet(other)[0]
        collector = b.AgvDurationCollector(fields={}, populations=()).attach(env)
        collector.observe_boundary(agv)  # no open interval yet: only starts one
        assert set(collector._intervals) == {agv.id}
        collector.observe_boundary(foreign)
        assert "another environment" in collector.failure
        collector.observe_boundary(agv)  # failed collectors ignore later boundaries
        assert set(collector._intervals) == {agv.id}


def test_created_and_retired_agvs_open_and_censor_intervals():
    from simulatte.intralogistics import AGV

    b = api()
    with Environment(seed=1) as env:
        first = _fleet(env)[0]
        collector = b.AgvDurationCollector(fields={}, populations=()).attach(env)
        env.activate()
        late_id = []

        def work():
            yield env.timeout(1)
            late = AGV(env=env, agv_type=first.agv_type, initial_node=first.current_node)
            late_id.append(late.id)
            yield env.timeout(1)
            env.entities.retire(late)
            yield env.timeout(1)

        env.process(work())
        env.run(until=5)
    ended = [(r["agv_id"], r["start"], r["end"], r["censored"]) for r in collector.rows]
    assert ended == [(late_id[0], 1.0, 2.0, True), (first.id, 0.0, 5.0, True)]
    assert collector.failure is None


def test_failed_collector_does_not_close_open_intervals_and_rejects_bad_durations():
    b = api()
    with Environment(seed=1) as env:
        _fleet(env)
        collector = b.AgvDurationCollector(fields={}, populations=()).attach(env)
        env.activate()
        collector.failure = "earlier failure"
    assert collector.rows == () and collector._intervals == {}
    for duration in (-1.0, float("inf"), float("nan"), True):
        with pytest.raises(ValueError, match="nonnegative"):
            b.fleet_utilization([dict(agv_id="a", state="IDLE", duration=duration)])
