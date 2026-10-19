"""Streaming descriptive summaries and replication-level statistical tables."""

import math
from . import codec
from .outputs import Table
from .types import DatasetSchema, Field


def _dtype(value):
    if value is None:
        return "null"
    if type(value) in (str, bool, int, float):
        return {str: "string", bool: "bool", int: "int64", float: "float64"}[type(value)]
    raise ValueError("Group values must be scalar retained attributes")


def summarize_population(rows, *, value: str, group_by: tuple[str, ...] = (), unit=None, population="all") -> Table:
    """Finite-value descriptive summaries. Within-run observations never create t intervals.

    Memory is bounded by 10,000 groups, independent of observation count. Missing
    and non-finite values are counted separately, not converted into zero samples.
    """
    groups, types = {}, {}
    if not group_by:
        groups["all"] = ({}, [0, 0, 0, None, None, None])
    for row in rows:
        if any(name not in row for name in (*group_by, value)):
            raise ValueError("Required field was not retained")
        values = {name: row[name] for name in group_by}
        for name, item in values.items():
            dtype = _dtype(item)
            if dtype != "null":
                if name in types and types[name] != dtype:
                    raise ValueError("Group field has inconsistent scalar types")
                types[name] = dtype
        key = codec.identity(values) if group_by else "all"
        if key not in groups:
            if len(groups) >= 10_000:
                raise ValueError("Summary exceeds 10000 groups; select fewer groups")
            groups[key] = (values, [0, 0, 0, None, None, None])
        _, stats = groups[key]
        item = row[value]
        if item is None:
            stats[1] += 1
        elif type(item) not in (int, float):
            raise ValueError("Summary value must be numerical or missing")
        elif not math.isfinite(item):
            stats[2] += 1
        else:
            n, mean = stats[0] + 1, stats[3]
            number = float(item)
            stats[0] = n
            stats[3] = number if mean is None else mean * ((n - 1) / n) + number / n
            stats[4] = number if stats[4] is None else min(stats[4], number)
            stats[5] = number if stats[5] is None else max(stats[5], number)
    fields = tuple(Field(name, types.get(name, "null"), nullable=True) for name in group_by)
    fields += tuple(Field(name, "int64") for name in ("n", "missing", "nonfinite"))
    fields += tuple(Field(name, "float64", unit=unit, nullable=True) for name in ("mean", "min", "max"))
    output = [
        {**values, **dict(zip(("n", "missing", "nonfinite", "mean", "min", "max"), stats, strict=True))}
        for values, stats in groups.values()
    ]
    return Table(
        DatasetSchema("population_summary", fields),
        output,
        {
            "population": population,
            "value": value,
            "unit": unit,
            "inferential_convention": "pooled_descriptive",
            "group_by": list(group_by),
            "distribution_representation": "summary_only",
        },
    )


def compare_replications(
    snapshot, *, metric: str, baseline: str | None = None, level: float = 0.95, bonferroni: bool = False
) -> Table:
    """Exactly one scalar per replication; paired comparisons use replication indices."""
    from simulatte.experiments.results import ExperimentReport

    report = ExperimentReport.from_snapshot(snapshot)
    report._kpis = [metric]
    rows = (
        report.table(level, bonferroni=bonferroni)
        if baseline is None
        else report.paired(baseline, level, bonferroni=bonferroni)
    )
    output = []
    for row in rows:
        estimate = row.estimate if baseline is None else row.paired.estimate
        missing = [] if baseline is None else row.paired.missing
        output.append(
            {
                "configuration": row.configuration,
                "label": row.label,
                "metric": metric,
                "n": estimate.n,
                "state": estimate.state,
                "mean": estimate.mean,
                "lower": estimate.lower,
                "upper": estimate.upper,
                "level": estimate.level,
                "missing_indices": list(missing),
            }
        )
    fields = (
        Field("configuration", "int64"),
        Field("label", "string"),
        Field("metric", "string"),
        Field("n", "int64"),
        Field("state", "string"),
        *(Field(name, "float64", nullable=True) for name in ("mean", "lower", "upper")),
        Field("level", "float64"),
        Field("missing_indices", {"list": "int64"}),
    )
    return Table(
        DatasetSchema("replication_comparison", fields, observation="replication"),
        output,
        {
            "inferential_convention": "replication_t" if baseline is None else "replication_paired_t",
            "baseline": baseline,
            "pointwise": not bonferroni,
            "family_size": len(rows),
            "requested_level": level,
            "partial_snapshot": snapshot["partial"],
            "missing_replications": [r.to_dict() for r in report.missing()],
        },
    )
