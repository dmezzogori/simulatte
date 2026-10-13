"""Experiment results: per-configuration intervals and paired comparisons (design 5, D98).

Observations are the replication-level KPI scalars of succeeded or reused replications;
samples inside a run are never read. Configuration labels, counts and the
``mixed_runtime`` flags come from :func:`simulatte.execution.scheduler.report`.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from simulatte.execution import scheduler
from simulatte.execution.store import RunStore
from simulatte.statistics import Estimate, Paired, bonferroni, paired, summarize

# Longest ``missing`` and ``replications`` lists a rendered document carries; ``*_total`` give the full counts.
MAX_LISTED = 1000
_SUCCESS = ("succeeded", "reused")


@dataclass(frozen=True)
class Row:
    """One configuration and KPI: the estimate over the replications that reported it."""

    configuration: int
    label: str
    kpi: str
    estimate: Estimate
    n_succeeded: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration": self.configuration,
            "label": self.label,
            "kpi": self.kpi,
            "n_succeeded": self.n_succeeded,
            "estimate": self.estimate.to_dict(),
        }


@dataclass(frozen=True)
class PairedRow:
    """One configuration against the baseline on one KPI (differences are configuration - baseline)."""

    configuration: int
    label: str
    kpi: str
    paired: Paired

    def to_dict(self) -> dict[str, Any]:
        # The per-index differences stay out of the document to keep it bounded.
        return {
            "configuration": self.configuration,
            "label": self.label,
            "kpi": self.kpi,
            "n": self.paired.n,
            "estimate": self.paired.estimate.to_dict(),
            "missing": self.paired.missing,
        }


@dataclass(frozen=True)
class Missing:
    """A replication that has no result (failed, cancelled, lost, or not finished)."""

    configuration: int
    index: int
    status: str
    attempt: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration": self.configuration,
            "index": self.index,
            "status": self.status,
            "attempt": self.attempt,
        }


def _scalar(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


class ExperimentReport:
    """Statistics over the stored results of one experiment run."""

    def __init__(self, store: RunStore, run_id: str):
        self.run_id = run_id
        self._summary = scheduler.report(store, run_id)
        self._replications = store.replications(run_id)
        scalars = store.replication_kpis(run_id)
        self._kpis = sorted({name for values in scalars.values() for name in values})
        # observations[configuration][kpi][index]: a KPI a replication did not report is simply absent.
        self._observations: list[dict[str, dict[int, float]]] = [{} for _ in self._summary["configurations"]]
        for (configuration, index), values in scalars.items():
            for name, value in values.items():
                if _scalar(value):
                    self._observations[configuration].setdefault(name, {})[index] = float(value)
        self._succeeded = [0] * len(self._observations)
        for item in self._replications:
            self._succeeded[item["configuration"]] += item["status"] in _SUCCESS

    @property
    def status(self) -> str:
        return self._summary["status"]

    def _labels(self) -> list[str]:
        return [item["label"] for item in self._summary["configurations"]]

    def table(self, level: float, *, bonferroni: bool = False) -> list[Row]:
        """A row per configuration and KPI; ``bonferroni`` adjusts for the number of rows."""
        labels = self._labels()
        effective = self._level(level, len(labels) * len(self._kpis), bonferroni)
        return [
            Row(
                configuration,
                labels[configuration],
                kpi,
                summarize(self._observations[configuration].get(kpi, {}).values(), level=effective),
                self._succeeded[configuration],
            )
            for configuration in range(len(labels))
            for kpi in self._kpis
        ]

    def paired(self, baseline_label: str, level: float, *, bonferroni: bool = False) -> list[PairedRow]:
        """Each other configuration minus the baseline, per KPI, paired by replication index."""
        labels = self._labels()
        if baseline_label not in labels:
            raise ValueError(f"Unknown baseline {baseline_label!r}; configurations: {', '.join(labels)}")
        baseline = labels.index(baseline_label)
        others = [configuration for configuration in range(len(labels)) if configuration != baseline]
        effective = self._level(level, len(others) * len(self._kpis), bonferroni)
        return [
            PairedRow(
                configuration,
                labels[configuration],
                kpi,
                paired(
                    self._observations[baseline].get(kpi, {}),
                    self._observations[configuration].get(kpi, {}),
                    level=effective,
                ),
            )
            for configuration in others
            for kpi in self._kpis
        ]

    def missing(self) -> list[Missing]:
        """Replications without a result, in dispatch order."""
        return [
            Missing(item["configuration"], item["index"], item["status"], item["attempt"])
            for item in self._replications
            if item["status"] not in _SUCCESS
        ]

    @staticmethod
    def _level(level: float, m: int, adjust: bool) -> float:
        return bonferroni(level, max(m, 1)) if adjust else level

    def to_dict(self, level: float = 0.95, *, baseline: str | None = None, bonferroni: bool = False) -> dict[str, Any]:
        """The report as plain data. ``pointwise`` is False only when ``bonferroni`` adjusts the level.

        ``m`` and ``effective_level`` describe the table's family of intervals; a baseline adds
        ``paired_m`` and ``paired_effective_level`` for the comparison family.
        """
        rows = self.table(level, bonferroni=bonferroni)
        missing = self.missing()
        document: dict[str, Any] = {
            "run_id": self.run_id,
            "name": self._summary["name"],
            "status": self.status,
            "level": level,
            "effective_level": self._level(level, len(rows), bonferroni),
            "pointwise": not bonferroni,
            "m": len(rows),
            "configurations": self._summary["configurations"],
            "rows": [row.to_dict() for row in rows],
            "missing": [item.to_dict() for item in missing[:MAX_LISTED]],
            "missing_total": len(missing),
            # Dispatch order with attempt ids, so a client can open any committed replication.
            "replications": [
                {key: item[key] for key in ("configuration", "index", "status", "attempt")}
                for item in self._replications[:MAX_LISTED]
            ],
            "replications_total": len(self._replications),
        }
        if baseline is not None:
            comparisons = self.paired(baseline, level, bonferroni=bonferroni)
            document["baseline"] = baseline
            document["paired_m"] = len(comparisons)
            document["paired_effective_level"] = self._level(level, len(comparisons), bonferroni)
            document["paired"] = [item.to_dict() for item in comparisons]
        return document
