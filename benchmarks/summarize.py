"""Print a Markdown table of ``run.py`` and ``bench_sampling.py`` results (time, memory, trace, seeks, sampling).

Usage::

    python benchmarks/summarize.py results/*.json >> "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

COLUMNS = (
    "| Interpreter | Workload | Version | Mode | Median (IQR) | Peak RSS | Trace | Chunks | Seek p50 / p95 "
    "| Jobs/s | Draws/s |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|\n"
)


def _fmt(value: Any, spec: str, suffix: str = "") -> str:
    return "–" if value is None else f"{value:{spec}}{suffix}"


def row(result: dict[str, Any]) -> str:
    python = result["python"]
    workload = Path(result["workload"]["path"]).name
    trace = result.get("trace_bytes")
    seek = "–"
    if result.get("seek_p50_ms") is not None:
        seek = f"{result['seek_p50_ms']:.1f} / {result['seek_p95_ms']:.1f} ms"
    return (
        f"| {python['implementation']} {python['version']} | {workload} | {result['label']} | {result['mode']} "
        f"| {result['median_s']:.3f} s ({result['iqr_s']:.3f}) | {_fmt(result.get('peak_mb'), '.0f', ' MB')} "
        f"| {_fmt(None if trace is None else trace / 1e6, '.2f', ' MB')} | {_fmt(result.get('chunks'), 'd')} "
        f"| {seek} | {_fmt(result.get('jobs_per_s'), ',.0f')} | {_fmt(result.get('draws_per_s'), ',.0f')} |\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="+")
    args = parser.parse_args(argv)
    results = [json.loads(Path(path).read_text()) for path in sorted(args.results)]
    print(COLUMNS + "".join(row(result) for result in results), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
