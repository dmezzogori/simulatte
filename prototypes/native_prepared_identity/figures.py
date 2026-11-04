"""Export paired query/live results after all timing is finished."""

import argparse
import json
from pathlib import Path


def main(root):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    micro = json.loads((root / "micro-final/audit.json").read_text())
    live = json.loads((root / "live-final/audit.json").read_text())
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5), constrained_layout=True)
    groups = micro["live"]
    for ax, a, b, title in (
        (axes[0, 0], "unprepared_composition", "composition_only", "Identity composition (replay)"),
        (axes[0, 1], "unprepared_pipeline", "pipeline", "Complete query, validation retained"),
    ):
        x = list(range(len(groups)))
        for offset, field, label, color in ((-0.18, a, "Unprepared", "#526c8d"), (0.18, b, "Prepared", "#29856c")):
            ax.bar(
                [v + offset for v in x], [r["median_us"][field] for r in groups], width=0.34, label=label, color=color
            )
        ax.set_xticks(x, [r["fixture"] + "\n" + r["retention"] for r in groups])
        ax.set(title=title, ylabel="Unprofiled latency (µs)", ylim=(0, max(r["median_us"][a] for r in groups) * 1.3))
        ax.legend(loc="upper right", ncols=2, fontsize=8)
    pairs = live["dense_paired_costs"]
    x = list(range(len(pairs)))
    for offset, field, label, color in (
        (-0.18, "median_unprepared", "Unprepared", "#526c8d"),
        (0.18, "median_prepared", "Prepared", "#29856c"),
    ):
        axes[1, 0].bar([v + offset for v in x], [r[field] for r in pairs], width=0.34, label=label, color=color)
    axes[1, 0].set_xticks(x, [r["fixture"] + "\n" + f"{r['ticks']:,}" for r in pairs])
    axes[1, 0].set(title="Dense capture · complete Coordinator run", ylabel="Median wall seconds")
    if pairs:
        axes[1, 0].set_ylim(0, max(r["median_unprepared"] for r in pairs) * 1.3)
    axes[1, 0].legend(loc="upper right", ncols=2, fontsize=8)
    callbacks = micro["callback"]
    for retention, color in (("full", "#ad563e"), ("bounded", "#277b9b")):
        rows = [r for r in callbacks if r["retention"] == retention]
        axes[1, 1].plot(
            [r["callback_bytes"] for r in rows],
            [100 * (1 - r["median_us"]["pipeline"] / r["median_us"]["unprepared_pipeline"]) for r in rows],
            "o-",
            label=retention,
            color=color,
        )
    axes[1, 1].set(
        title="Callback-size contrast · query time reduction",
        xlabel="Encoded callback bytes",
        ylabel="Reduction from unprepared (%)",
        xscale="log",
    )
    axes[1, 1].legend()
    axes[1, 1].grid(alpha=0.2)
    fig.suptitle("One prepared canonical identity per query · same bytes and validator")
    fig.savefig(root / "prepared.png", dpi=180)
    fig.savefig(root / "prepared.svg")
    plt.close(fig)
    svg = root / "prepared.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    main(parser.parse_args().root)
