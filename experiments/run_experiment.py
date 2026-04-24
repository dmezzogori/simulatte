"""CLI for running jobshop policy experiments."""

from __future__ import annotations

import argparse
import csv
import sys
from functools import partial
from itertools import product
from pathlib import Path

import yaml
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))  # simulatte package
sys.path.insert(0, str(Path(__file__).parent))  # experiments/extractors

from simulatte.builders import (
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
    spt_priority_policy,
)
from simulatte.runner import Runner

from extractors import extract_results


def _immediate_fifo_runs(cfg: dict) -> list[tuple[dict, Runner]]:
    sim = cfg["simulation"]
    extract = partial(extract_results, sim["warmup"])
    builder = partial(
        build_immediate_release_system,
        n_servers=sim["n_servers"],
        arrival_rate=sim["arrival_rate"],
        service_rate=sim["service_rate"],
    )
    return [({}, Runner(builder=builder, seeds=sim["seeds"], extract_fn=extract, parallel=True))]


def _immediate_spt_runs(cfg: dict) -> list[tuple[dict, Runner]]:
    sim = cfg["simulation"]
    extract = partial(extract_results, sim["warmup"])
    builder = partial(
        build_immediate_release_system,
        n_servers=sim["n_servers"],
        arrival_rate=sim["arrival_rate"],
        service_rate=sim["service_rate"],
        priority_policies=spt_priority_policy,
    )
    return [({}, Runner(builder=builder, seeds=sim["seeds"], extract_fn=extract, parallel=True))]


def _slar_runs(cfg: dict) -> list[tuple[dict, Runner]]:
    sim = cfg["simulation"]
    pol = cfg["policies"]["slar"]
    extract = partial(extract_results, sim["warmup"])
    runs = []
    for af in pol["allowance_factor"]:
        builder = partial(
            build_slar_system,
            allowance_factor=af,
            n_servers=sim["n_servers"],
            arrival_rate=sim["arrival_rate"],
            service_rate=sim["service_rate"],
        )
        runner = Runner(builder=builder, seeds=sim["seeds"], extract_fn=extract, parallel=True)
        runs.append(({"allowance_factor": af}, runner))
    return runs


def _lumscor_runs(cfg: dict) -> list[tuple[dict, Runner]]:
    sim = cfg["simulation"]
    pol = cfg["policies"]["lumscor"]
    extract = partial(extract_results, sim["warmup"])
    runs = []
    for wl, af in product(pol["wl_norm_level"], pol["allowance_factor"]):
        builder = partial(
            build_lumscor_system,
            wl_norm_level=wl,
            allowance_factor=af,
            check_timeout=pol["check_timeout"],
            n_servers=sim["n_servers"],
            arrival_rate=sim["arrival_rate"],
            service_rate=sim["service_rate"],
        )
        runner = Runner(builder=builder, seeds=sim["seeds"], extract_fn=extract, parallel=True)
        runs.append(({"wl_norm_level": wl, "allowance_factor": af, "check_timeout": pol["check_timeout"]}, runner))
    return runs


POLICY_REGISTRY: dict[str, Callable[[dict], list[tuple[dict, Runner]]]] = {
    "immediate_fifo": _immediate_fifo_runs,
    "immediate_spt": _immediate_spt_runs,
    "slar": _slar_runs,
    "lumscor": _lumscor_runs,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a jobshop policy experiment.")
    parser.add_argument("policy", choices=list(POLICY_REGISTRY), help="Policy to run")
    args = parser.parse_args()

    config_path = Path(__file__).parent / "experiment_config.yaml"
    with config_path.open() as f:
        cfg = yaml.safe_load(f)

    sim = cfg["simulation"]
    runs = POLICY_REGISTRY[args.policy](cfg)

    results_dir = Path(__file__).parent.parent / "results"
    results_dir.mkdir(exist_ok=True)
    csv_path = results_dir / f"{args.policy}.csv"

    all_rows = []
    for param_cols, runner in runs:
        results = runner.run(until=sim["run_until"])
        for seed, result in zip(sim["seeds"], results, strict=True):
            all_rows.append({"seed": seed, **param_cols, **result})

    if not all_rows:
        print(f"Warning: no results produced for policy '{args.policy}'", file=sys.stderr)
        return

    fieldnames = list(all_rows[0].keys())
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Saved {len(all_rows)} rows to {csv_path}")


if __name__ == "__main__":
    main()
