"""Experiment reports: per-configuration intervals, paired comparisons and missing replications from a store."""

from dataclasses import replace
import json
import math

import pytest

from simulatte.execution import schema
from simulatte.execution.store import RunStore, _blob
from simulatte.experiments.results import ExperimentReport, Missing
from simulatte.statistics import bonferroni, t_quantile
from tests.execution.test_schema import request
from tests.execution.test_store_slots import DEFINITION, advance, fixture_copy

LABELS = ("x=1/base", "x=2/base")
# (configuration, index) -> KPI scalars, "failed" or "pending".
OUTCOMES = {
    (0, 0): {"shop/wip": 10.0, "shop/tp": 1.0},
    (0, 1): {"shop/wip": 12.0, "shop/tp": 2.0},
    (0, 2): {"shop/wip": 14.0, "shop/tp": None},  # reported as no observation
    (1, 0): {"shop/wip": 11.0, "shop/tp": 2.0},
    (1, 1): "failed",
    (1, 2): {"shop/wip": 17.0},
    (1, 3): "pending",
}


def seeded(configuration, index):
    return request(schema, seed=100 * configuration + index)


def build(store, outcomes=OUTCOMES, hashes=None):
    """A run whose replications end as ``outcomes`` says; returns ``(run_id, attempts by replication)``."""
    configurations = [
        {"label": label, "parameters": {"x": float(x)}, "layout_hash": "c" * 64, "layout_ref": None}
        for x, label in enumerate(LABELS, start=1)
    ]
    replications = [
        {"configuration": c, "index": i, "seed": i, "content_ids": [schema.content_id(seeded(c, i))]}
        for c, i in outcomes
    ]
    run = store.create_experiment_run(store.put_experiment(DEFINITION), {}, configurations, replications)
    revision = store.current_revision()
    attempts = {}
    for (c, i), outcome in outcomes.items():
        if outcome == "pending":
            continue
        key = store.assign(
            replace(seeded(c, i), layout_revision=revision.number), revision, slot="local-1", pinned=True
        )
        store.bind_replication(run, c, i, key)
        attempts[c, i] = key.attempt_id
        if outcome == "failed":
            advance(store, key, "running")
            store.revoke(key, "failed")
            store.set_replication_status(run, c, i, "failed", attempt=key.attempt_id)
            continue
        advance(store, key, "finalizing")
        manifest = {"simulation": {"dependencies": {"hash": (hashes or {}).get(c, "d" * 64)}}}
        with store._transaction() as db:
            db.execute("INSERT INTO results VALUES (?,?,?,?)", (key.attempt_id, _blob(manifest), _blob({}), 1.0))
            assert isinstance(outcome, dict)
            for name, value in outcome.items():
                db.execute("INSERT INTO kpis VALUES (?,?,?)", (key.attempt_id, name, _blob(value)))
            db.execute("UPDATE attempts SET status='succeeded' WHERE id=?", (key.attempt_id,))
            db.execute("UPDATE slots SET active=NULL WHERE active=?", (key.attempt_id,))
        store.set_replication_status(run, c, i, "succeeded", attempt=key.attempt_id)
    return run, attempts


@pytest.fixture
def store(tmp_path):
    with RunStore.open(fixture_copy(tmp_path)) as opened:
        yield opened


def by_key(rows):
    return {(row.label, row.kpi): row for row in rows}


def test_table_reports_n_and_omits_intervals_below_two_observations(store):
    run, _ = build(store)
    rows = by_key(ExperimentReport(store, run).table(0.95))
    assert sorted(rows) == [(label, kpi) for label in LABELS for kpi in ("shop/tp", "shop/wip")]
    wip = rows["x=1/base", "shop/wip"]
    assert (wip.configuration, wip.n_succeeded) == (0, 3)
    assert (wip.estimate.n, wip.estimate.mean, wip.estimate.sd, wip.estimate.state) == (3, 12.0, 2.0, "ok")
    assert wip.estimate.half_width == pytest.approx(t_quantile(0.975, 2) * 2.0 / math.sqrt(3))
    # shop/tp is absent from replication 2 of configuration 0: it counts in n_succeeded but not in the estimate.
    assert (rows["x=1/base", "shop/tp"].n_succeeded, rows["x=1/base", "shop/tp"].estimate.n) == (3, 2)
    assert rows["x=1/base", "shop/tp"].estimate.mean == 1.5
    # One failed and one pending replication of configuration 1: two succeeded, one with shop/tp.
    assert rows["x=2/base", "shop/wip"].n_succeeded == 2
    assert rows["x=2/base", "shop/wip"].estimate.mean == 14.0
    single = rows["x=2/base", "shop/tp"].estimate
    assert (single.state, single.n, single.mean, single.lower, single.upper) == ("insufficient", 1, 2.0, None, None)


def test_missing_lists_unsuccessful_replications_with_status_and_attempt(store):
    run, attempts = build(store)
    assert ExperimentReport(store, run).missing() == [
        Missing(configuration=1, index=1, status="failed", attempt=attempts[1, 1]),
        Missing(configuration=1, index=3, status="pending", attempt=None),
    ]


def test_paired_differences_pair_by_replication_index(store):
    run, _ = build(store)
    (tp, wip) = ExperimentReport(store, run).paired("x=1/base", 0.95)
    assert (wip.configuration, wip.label, wip.kpi) == (1, "x=2/base", "shop/wip")
    # b - a over indices 0 and 2; index 1 failed in configuration 1.
    assert wip.paired.differences == {0: 1.0, 2: 3.0}
    assert wip.paired.missing == [1]
    assert wip.paired.estimate.mean == 2.0 and wip.paired.estimate.n == 2
    assert tp.kpi == "shop/tp" and tp.paired.differences == {0: 1.0}
    assert tp.paired.missing == [1] and tp.paired.estimate.state == "insufficient"
    # The other direction flips the sign.
    reverse = ExperimentReport(store, run).paired("x=2/base", 0.95)
    assert reverse[1].configuration == 0 and reverse[1].paired.differences == {0: -1.0, 2: -3.0}


def test_unknown_baseline_and_run(store):
    run, _ = build(store)
    with pytest.raises(ValueError, match="nowhere"):
        ExperimentReport(store, run).paired("nowhere", 0.95)
    with pytest.raises(KeyError):
        ExperimentReport(store, "0" * 64)


def test_bonferroni_lowers_the_effective_level_and_clears_pointwise(store):
    run, _ = build(store)
    report = ExperimentReport(store, run)
    plain = report.to_dict(0.95)
    assert plain["pointwise"] is True and plain["m"] == 4 and plain["effective_level"] == 0.95
    adjusted = report.to_dict(0.95, baseline="x=1/base", bonferroni=True)
    assert adjusted["pointwise"] is False and adjusted["level"] == 0.95
    assert adjusted["m"] == 4 and adjusted["effective_level"] == pytest.approx(bonferroni(0.95, 4))
    assert {row["estimate"]["level"] for row in adjusted["rows"]} == {adjusted["effective_level"]}
    assert adjusted["paired_m"] == 2 and adjusted["paired_effective_level"] == pytest.approx(bonferroni(0.95, 2))
    assert {item["estimate"]["level"] for item in adjusted["paired"]} == {adjusted["paired_effective_level"]}
    wide = by_key(report.table(0.95, bonferroni=True))["x=1/base", "shop/wip"].estimate
    assert wide.half_width > by_key(report.table(0.95))["x=1/base", "shop/wip"].estimate.half_width


def test_to_dict_is_bounded_json_with_runtime_flags(store):
    run, attempts = build(store, hashes={1: "e" * 64})
    document = ExperimentReport(store, run).to_dict(0.9, baseline="x=1/base")
    assert json.loads(json.dumps(document, allow_nan=False)) == document
    assert document["run_id"] == run and document["status"] == "running" and document["level"] == 0.9
    assert document["missing"] == [
        {"configuration": 1, "index": 1, "status": "failed", "attempt": attempts[1, 1]},
        {"configuration": 1, "index": 3, "status": "pending", "attempt": None},
    ]
    assert document["missing_total"] == 2
    assert [item["mixed_runtime"] for item in document["configurations"]] == [False, False]
    assert document["rows"][0] == {
        "configuration": 0,
        "label": "x=1/base",
        "kpi": "shop/tp",
        "n_succeeded": 3,
        "estimate": document["rows"][0]["estimate"],
    }
    first = document["paired"][0]
    assert set(first) == {"configuration", "label", "kpi", "n", "estimate", "missing"}
    assert not {"paired", "paired_m", "baseline"} & set(ExperimentReport(store, run).to_dict(0.9))


def test_mixed_runtime_flag_comes_from_the_scheduler_report(store):
    run, attempts = build(store, {(0, 0): {"shop/wip": 1.0}, (0, 1): {"shop/wip": 2.0}})
    with store._transaction() as db:
        db.execute(
            "UPDATE results SET manifest=? WHERE attempt=?",
            (_blob({"simulation": {"dependencies": {"hash": "f" * 64}}}), attempts[0, 1]),
        )
    document = ExperimentReport(store, run).to_dict()
    assert [item["mixed_runtime"] for item in document["configurations"]] == [True, False]


def test_missing_list_in_documents_is_bounded(store, monkeypatch):
    from simulatte.experiments import results

    monkeypatch.setattr(results, "MAX_LISTED", 1)
    run, _ = build(store)
    document = ExperimentReport(store, run).to_dict()
    assert len(document["missing"]) == 1 and document["missing_total"] == 2


def test_run_without_results_has_no_rows(store):
    run, _ = build(store, {(0, 0): "pending", (1, 0): "pending"})
    report = ExperimentReport(store, run)
    assert report.table(0.95) == [] and report.paired("x=1/base", 0.95) == []
    document = report.to_dict()
    assert document["m"] == 0 and document["effective_level"] == 0.95
    assert len(document["missing"]) == 2
