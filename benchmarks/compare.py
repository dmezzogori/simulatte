"""Compare two ``run.py`` (or ``bench_sampling.py``) results and apply the overhead gate (spec §14, C1.9).

The overhead is ``median(head) / median(base) - 1``. When both results are mode ``none`` (the no-subscriber
comparison against ``simulatte==0.12.0``), the command exits with status 1 if the overhead exceeds
``--budget + --noise``; every other pair (``digest`` or ``full`` against ``none``, sampling) is reported only.
Both results must come from the same workload, interpreter and job/operation counts (status 2 otherwise).

Usage::

    python benchmarks/compare.py base.json head.json --budget 0.03 --noise 0.02 [--summary FILE]

A Markdown table (header and one row, or the row alone with ``--no-header``) is printed, and appended to
``--summary`` (for example ``$GITHUB_STEP_SUMMARY``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HEADER = (
    "| Interpreter | Workload | Base | Head | Base median (IQR) | Head median (IQR) "
    "| Ratio | Overhead | Limit | Verdict |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
)


def _load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def _name(result: dict[str, Any]) -> str:
    return f"{result['label']} `{result['mode']}`"


def _check_comparable(base: dict[str, Any], head: dict[str, Any]) -> str | None:
    """Why the two results are not comparable, or None."""
    if base["python"]["implementation"] != head["python"]["implementation"]:
        return "different interpreters"
    if base["workload"]["sha256"] != head["workload"]["sha256"]:
        return "different workloads"
    if base["counts"] != head["counts"]:
        return f"different job/operation counts or trajectories: {base['counts']} vs {head['counts']}"
    return None


def compare(base: dict[str, Any], head: dict[str, Any], *, budget: float, noise: float) -> tuple[str, bool]:
    """The Markdown row for the pair and whether the gate fails."""
    ratio = head["median_s"] / base["median_s"]
    overhead = ratio - 1
    gated = base["mode"] == head["mode"] == "none"
    limit = budget + noise
    failed = gated and overhead > limit
    if gated:
        verdict = "**FAIL**" if failed else "pass"
        limit_text = f"{limit:+.1%}"
    else:
        verdict = "info"
        limit_text = "–"
    python = head["python"]
    workload = Path(head["workload"]["path"]).name
    row = (
        f"| {python['implementation']} {python['version']} | {workload} | {_name(base)} | {_name(head)} "
        f"| {base['median_s']:.3f} s ({base['iqr_s']:.3f}) | {head['median_s']:.3f} s ({head['iqr_s']:.3f}) "
        f"| {ratio:.3f} | {overhead:+.1%} | {limit_text} | {verdict} |\n"
    )
    return row, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base")
    parser.add_argument("head")
    parser.add_argument("--budget", type=float, default=0.03, help="allowed median overhead of mode none")
    parser.add_argument("--noise", type=float, default=0.0, help="noise band added to the budget")
    parser.add_argument("--summary", help="also append the printed Markdown to this file")
    parser.add_argument("--no-header", action="store_true", help="print the row without the table header")
    args = parser.parse_args(argv)
    base, head = _load(args.base), _load(args.head)
    problem = _check_comparable(base, head)
    if problem is not None:
        print(f"compare.py: {args.base} and {args.head} are not comparable: {problem}", file=sys.stderr)
        return 2
    row, failed = compare(base, head, budget=args.budget, noise=args.noise)
    text = row if args.no_header else HEADER + row
    sys.stdout.write(text)
    if args.summary:
        with Path(args.summary).open("a") as f:
            f.write(text)
    if failed:
        print(
            f"compare.py: mode none overhead {head['median_s'] / base['median_s'] - 1:+.1%} exceeds the budget "
            f"{args.budget:.1%} + noise {args.noise:.1%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
