"""Standalone figures from audited paired measurements, with every seed visible."""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def main(root):
    summary = json.loads((root / "summary.json").read_text())
    assert summary["passed"] and not summary["quick"]
    plt.rcParams.update({"font.size": 10, "svg.fonttype": "none"})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), layout="constrained")
    rates = [0, 64, 16, 1]
    labels = ["Endpoints", "Every 64", "Every 16", "Every poll"]
    colors = {"helper": "#087e8b", "prepared": "#7251b5", "unprepared": "#d97732"}
    for ax, fixture in zip(axes, ("fixed", "coupled"), strict=True):
        rows = [next(r for r in summary["comparisons"] if r["fixture"] == fixture and r["every"] == q) for q in rates]
        for mode in ("unprepared", "prepared", "helper"):
            y = [r["metrics"]["cold_seconds"]["medians"][mode] for r in rows]
            ax.plot(range(4), y, "o-", label=mode, color=colors[mode])
        ax.set(
            xticks=range(4),
            xticklabels=labels,
            ylabel="Median cold Coordinator wall time (s)",
            title=fixture.capitalize() + " feedback model",
        )
        ax.grid(axis="y", alpha=0.25)
        ax.set_ylim(bottom=0)
    axes[0].legend(frameon=False)
    fig.suptitle("Public helper usage · 32,768 control polls · six paired seeds")
    save(fig, root / "usage-wall")
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), layout="constrained")
    for ax, fixture in zip(axes, ("fixed", "coupled"), strict=True):
        for index, q in enumerate(rates):
            r = next(r for r in summary["comparisons"] if r["fixture"] == fixture and r["every"] == q)
            ratios = r["metrics"]["cold_seconds"]["helper_over_prepared"]
            ax.scatter(
                index + np.linspace(-0.11, 0.11, len(ratios["values"])),
                100 * (np.array(ratios["values"]) - 1),
                s=25,
                color=colors["helper"],
                alpha=0.7,
            )
            ax.plot([index - 0.2, index + 0.2], [100 * (ratios["median"] - 1)] * 2, color="black", lw=2)
        ax.axhline(0, color="#888888", lw=1)
        ax.set(
            xticks=range(4),
            xticklabels=labels,
            ylabel="Public helper / frozen prepared − 1 (%)",
            title=fixture.capitalize() + " · paired samples and median",
        )
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("Result-object and detachment cost in complete managed runs")
    save(fig, root / "usage-paired")


def save(fig, path):
    fig.savefig(path.with_suffix(".svg"))
    fig.savefig(path.with_suffix(".png"), dpi=160)
    plt.close(fig)
    p = path.with_suffix(".svg")
    p.write_text("\n".join(line.rstrip() for line in p.read_text().splitlines()) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    main(p.parse_args().root)
