"""Report runner timing spread and process-bootstrap uncertainty from downloaded bench artifacts.

Uses independent process medians as resampling units; reports a two-sided 95% bootstrap interval
for head/base minus the observed ratio, in percentage points. This is empirical noise evidence,
not a promise about future hosted runners. Does not change accepted performance budgets.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path


def percentile(values: list[float], q: float) -> float:
    return sorted(values)[int((len(values) - 1) * q)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dirs", nargs="+", type=Path)
    parser.add_argument("--draws", type=int, default=10000)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if args.draws < 100:
        parser.error("--draws must be at least 100")
    rng = random.Random(20261010)
    rows = []
    for directory in args.artifact_dirs:
        for head_path in sorted(directory.glob("head-none-*.json")):
            head = json.loads(head_path.read_text())
            for label in ("base", "stripped"):
                base = json.loads(head_path.with_name(head_path.name.replace("head-", label + "-", 1)).read_text())
                h, b = head["process_medians_s"], base["process_medians_s"]
                observed = statistics.median(h) / statistics.median(b)
                errors = [
                    100
                    * (
                        statistics.median(rng.choices(h, k=len(h))) / statistics.median(rng.choices(b, k=len(b)))
                        - observed
                    )
                    for _ in range(args.draws)
                ]
                rows.append(
                    {
                        "artifacts": str(directory),
                        "workload": head_path.stem.removeprefix("head-none-"),
                        "baseline": label,
                        "python": head["python"],
                        "commit": head["commit"],
                        "processes": [len(h), len(b)],
                        "pooled_overhead_pct": 100 * (head["median_s"] / base["median_s"] - 1),
                        "head_iqr_pct": 100 * head["iqr_s"] / head["median_s"],
                        "base_iqr_pct": 100 * base["iqr_s"] / base["median_s"],
                        "noise_interval_pp": [percentile(errors, 0.025), percentile(errors, 0.975)],
                    }
                )
    if not rows:
        parser.error("no head-none benchmark artifacts found")
    report = json.dumps({"draws": args.draws, "unit": "process median", "rows": rows}, indent=2) + "\n"
    if args.json:
        args.json.write_text(report)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
