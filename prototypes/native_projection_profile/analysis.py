"""Audit raw measurements and summarize self-time without inclusive double counting."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess

from .run import sources

BASE = "7580a00817bfcd5fc9337d9377d1b216392db3c3"
REPO = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text())


def latency(query, stage, clock="wall"):
    return statistics.median(v[clock + "_ns"] / v["calls"] / 1000 for v in query["batches"][stage])


def summarize(value):
    query = value["query"]
    times = {key: latency(query, key) for key in query["batches"]}
    profile_summary = {}
    for key, profile in query["profiles"].items():
        total = profile["accounted_seconds"]
        assert abs(sum(profile["self_categories"].values()) - total) < 1e-7
        assert profile["calls"] > 0 and len(profile["functions"]) > 0
        profile_summary[key] = dict(
            self_percent={k: 100 * v / total for k, v in profile["self_categories"].items()},
            distortion=profile["elapsed_wall_ns"] / profile["calls"] / 1000 / times[key],
            counts_per_query={
                name: sum(r["total_calls"] for r in profile["functions"] if r["name"] == name) / profile["calls"]
                for name in ("freeze", "_canonical_map", "canonical_pack", "_detached", "plain")
            },
        )
    components = times["managed_hook"] + times["composition_only"] + times["validation_only"]
    return dict(
        parameters=value["parameters"],
        context=query["context"],
        wall_us=times,
        cpu_us={key: latency(query, key, "cpu") for key in query["batches"]},
        replay_closure_percent=100 * (components - times["pipeline"]) / times["pipeline"],
        profiles=profile_summary,
        python_peaks={k: v["peak_bytes"] for k, v in query["traced_python_memory"].items()},
    )


def median_rows(rows, keys):
    return {key: statistics.median(r["wall_us"][key] for r in rows) for key in keys}


def audit(output, plot=False):
    meta, cert, index = read(output / "metadata.json"), read(output / "verification.json"), read(output / "index.json")
    assert meta["source"] == sources() and cert["source_sha256"] == meta["source_sha256"]
    for name, digest in cert["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest, name
    rows, pids = [], []
    for item in index:
        path = output / f"cell-{item['cell']:02d}"
        value = read(path / "result.json")
        pids.append(value["pid"])
        assert value["parameters"] == item["parameters"]
        assert value["baseline"] == value["profiled"]
        query = value["query"]
        assert query["gc_enabled"] and len(query["batches"]) == 17
        assert query == read(path / "profiled/profiles/query.json")
        assert "site-packages/simulatte_native/_bin/" in value["origin"]
        for values in query["batches"].values():
            assert len(values) == meta["blocks"]
            assert sorted(v["block"] for v in values) == list(range(meta["blocks"]))
            assert all(v["calls"] > 0 and v["wall_ns"] > 0 and v["cpu_ns"] > 0 for v in values)
        rows.append(summarize(value))
    assert len(pids) == len(set(pids))
    live, histories, callbacks = [], [], []
    stages = (
        "native_atomic",
        "stream_hook",
        "managed_hook",
        "composition_only",
        "validation_only",
        "pipeline",
        "callback_only",
        "callback_freeze",
        "callback_prepared_pack",
        "detach_view",
        "identity_size_pack",
    )
    for fixture in ("fixed", "coupled"):
        for retention in ("full", "bounded"):
            group = [
                r
                for r in rows
                if r["parameters"]["kind"] == "live"
                and r["parameters"]["fixture"] == fixture
                and r["parameters"]["retention"] == retention
            ]
            if group:
                live.append(
                    dict(
                        fixture=fixture,
                        retention=retention,
                        cells=len(group),
                        median_us=median_rows(group, stages),
                        pipeline_self_percent={
                            k: statistics.mean(r["profiles"]["pipeline"]["self_percent"].get(k, 0) for r in group)
                            for k in sorted({k for r in group for k in r["profiles"]["pipeline"]["self_percent"]})
                        },
                    )
                )
    for retention in ("full", "bounded"):
        for n in (128, 8192, 65536):
            group = [
                r
                for r in rows
                if r["parameters"]["kind"] == "history"
                and r["parameters"]["retention"] == retention
                and r["parameters"]["jobs"] == n
            ]
            if group:
                histories.append(
                    dict(
                        retention=retention,
                        jobs=n,
                        cells=len(group),
                        median_us=median_rows(group, stages),
                        native_bytes=statistics.median(r["context"]["native_bytes"] for r in group),
                    )
                )
        for n in (0, 16, 256):
            group = [
                r
                for r in rows
                if r["parameters"]["retention"] == retention
                and r["parameters"]["jobs"] == 128
                and r["parameters"]["padding"] == n
                and r["parameters"]["kind"] != "live"
            ]
            if group:
                callbacks.append(
                    dict(
                        retention=retention,
                        padding=n,
                        cells=len(group),
                        median_us=median_rows(group, stages),
                        callback_bytes=statistics.median(r["context"]["callback_bytes"] for r in group),
                    )
                )
    # Only the callback payload changes at fixed history/seed: native and actions stay equal.
    padded_checks = 0
    for row in rows:
        p = row["parameters"]
        if p["kind"] != "callback":
            continue
        base = next(
            (
                r
                for r in rows
                if r["parameters"]["kind"] == "history"
                and r["parameters"]["jobs"] == 128
                and r["parameters"]["retention"] == p["retention"]
                and r["parameters"]["seed"] == p["seed"]
            ),
            None,
        )
        if base:
            assert row["context"]["state"]["native"] == base["context"]["state"]["native"]
            assert row["context"]["actions"] == base["context"]["actions"]
            assert row["context"]["state"]["authority"] != base["context"]["state"]["authority"]
            padded_checks += 1
    # Existing primary cases provide a historical endpoint oracle for two of the three seeds.
    previous = read(REPO / "prototypes/native_provider_projection/results/macos-final/gate.json")["rows"]
    matched = 0
    for item in index:
        p = item["parameters"]
        if p["kind"] == "live" and p["seed"] < 102:
            old = next(
                r
                for r in previous
                if r["parameters"]["fixture"] == p["fixture"]
                and r["parameters"]["engine"] == p["retention"]
                and r["parameters"]["policy"] == p["policy"]
                and r["seed"] == p["seed"]
                and r["warmup"] == 10
            )
            value = read(output / f"cell-{item['cell']:02d}" / "result.json")["profiled"]
            assert value["fingerprint"] == old["fingerprint"]
            assert value["artifacts"] == old["artifact_sha256"]
            matched += 1
    preserved = 0
    for record in subprocess.check_output(["git", "ls-tree", "-rz", BASE], cwd=REPO).split(b"\0"):
        if not record:
            continue
        fields, name = record.split(b"\t", 1)
        mode, _, blob = fields.split()
        path = REPO / name.decode()
        data = os.readlink(path).encode() if mode == b"120000" else path.read_bytes()
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == blob.decode(), name
        preserved += 1
    result = dict(
        passed=True,
        cells=len(rows),
        baseline_profiled_runs=2 * len(rows),
        historical_cases_matched=matched,
        fixed_native_callback_stress_checks=padded_checks,
        preserved_base_files=preserved,
        live=live,
        history=histories,
        callback=callbacks,
        per_cell=rows,
    )
    (output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    if plot:
        figure(result, output)
    print(json.dumps({k: v for k, v in result.items() if k != "per_cell"}, indent=2))
    return result


def figure(result, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    live = result["live"]
    x = list(range(len(live)))
    for i, (stage, label) in enumerate(
        (
            ("managed_hook", "Managed hook"),
            ("composition_only", "Composition replay"),
            ("validation_only", "Validation replay"),
            ("pipeline", "Actual pipeline"),
        )
    ):
        axes[0, 0].bar([v + i * 0.2 - 0.3 for v in x], [r["median_us"][stage] for r in live], width=0.19, label=label)
    axes[0, 0].set_xticks(x, [r["fixture"] + "\n" + r["retention"] for r in live])
    axes[0, 0].set(ylabel="Unprofiled query latency (µs)", title="Live boundary: hook, composition and pipeline")
    axes[0, 0].legend(fontsize=8)
    for retention, color in (("full", "#ad563e"), ("bounded", "#277b9b")):
        rows = [r for r in result["history"] if r["retention"] == retention]
        axes[0, 1].plot(
            [r["jobs"] for r in rows], [r["median_us"]["pipeline"] for r in rows], "o-", label=retention, color=color
        )
        rows = [r for r in result["callback"] if r["retention"] == retention]
        axes[1, 0].plot(
            [r["callback_bytes"] for r in rows],
            [r["median_us"]["pipeline"] for r in rows],
            "o-",
            label=retention,
            color=color,
        )
    axes[0, 1].set(
        xscale="log",
        yscale="log",
        xlabel="Completed native jobs",
        ylabel="Pipeline latency (µs)",
        title="Constant-shape callback; retained-history contrast",
    )
    axes[1, 0].set(
        xscale="log",
        yscale="log",
        xlabel="Encoded callback bytes",
        ylabel="Pipeline latency (µs)",
        title="Fixed 128-job history; callback-payload contrast",
    )
    for ax in (axes[0, 1], axes[1, 0]):
        ax.legend()
        ax.grid(alpha=0.2)
    bottom = [0.0] * len(live)
    for category in (
        "wire_preparation",
        "messagepack",
        "detachment_access",
        "native_atomic",
        "callback",
        "sha256",
        "other_glue",
        "harness",
    ):
        heights = [r["pipeline_self_percent"].get(category, 0) for r in live]
        axes[1, 1].bar(x, heights, bottom=bottom, label=category.replace("_", " "))
        bottom = [a + b for a, b in zip(bottom, heights)]
    axes[1, 1].set_xticks(x, [r["fixture"] + "\n" + r["retention"] for r in live])
    axes[1, 1].set(
        ylabel="Instrumented self-time share (%)",
        title="cProfile attribution (not unprofiled time shares)",
        ylim=(0, 100),
    )
    axes[1, 1].legend(fontsize=7, loc="upper center", ncols=2)
    fig.suptitle("Native projection packing/copying · CPython/macOS · frozen-state queries")
    fig.savefig(output / "profile.png", dpi=180)
    fig.savefig(output / "profile.svg")
    plt.close(fig)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--plot", action="store_true")
    args = p.parse_args()
    audit(args.output, args.plot)
