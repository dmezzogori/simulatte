"""Summarize raw benchmark observations into an auditable CSV and Markdown tables."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics

from .benchmark import assert_equivalent


def summaries(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
    assert_equivalent(rows)
    result = []
    for row in rows:
        values = {
            phase: statistics.median(s[phase] for s in row["samples"])
            for phase in ("build", "run", "extract", "close", "total")
        }
        values.update(
            {
                k: row[k]
                for k in (
                    "case",
                    "backend",
                    "interpreter",
                    "jobs",
                    "operations",
                    "load",
                    "policy",
                    "mode",
                    "hook",
                    "recording_bytes",
                    "peak_rss_bytes",
                )
            }
        )
        times = [s["total"] for s in row["samples"]]
        values.update(
            jobs_per_second=row["jobs"] / values["total"],
            operations_per_second=row["operations"] / values["total"],
            total_min=min(times),
            total_max=max(times),
            callbacks=row["counters"]["callbacks"],
        )
        result.append(values)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    rows = summaries(args.input)
    if args.csv:
        with args.csv.open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    by_case = {
        (r["interpreter"], r["backend"], r["jobs"], r["load"], r["policy"], r["mode"], r["hook"]): r for r in rows
    }
    print("| Interpreter / backend | Mode | Hook | Total s | Jobs/s | RSS MiB | Build / run / extract / close ms |")
    print("|---|---|---|---:|---:|---:|---|")
    for row in rows:
        if (row["jobs"], row["load"], row["policy"]) != (50000, 0.95, "spt"):
            continue
        phases = " / ".join(f"{1000 * row[k]:.1f}" for k in ("build", "run", "extract", "close"))
        print(
            f"| {row['interpreter']} / {row['backend']} | {row['mode']} | {row['hook']} | "
            f"{row['total']:.4f} | {row['jobs_per_second']:,.0f} | "
            f"{row['peak_rss_bytes'] / 2**20:.1f} | {phases} |"
        )
    print("\nNative total throughput speedup ranges over equivalent current CPython Simulatte:")
    for mode in ("none", "kpi", "record"):
        for hook in ("builtin", "python"):
            ratios = []
            for row in rows:
                if (row["interpreter"], row["backend"], row["mode"], row["hook"]) == ("CPython", "native", mode, hook):
                    key = ("CPython", "simulatte", row["jobs"], row["load"], row["policy"], mode, hook)
                    ratios.append(by_case[key]["total"] / row["total"])
            if ratios:
                print(f"{mode} {hook}: {min(ratios):.2f}–{max(ratios):.2f}x (median {statistics.median(ratios):.2f}x)")


if __name__ == "__main__":
    main()
