"""Audit installed Coordinator evidence without rebuilding or changing the source."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess

from simulatte import Environment
from .producer import Producer, encoded
from .run import REPO, sources, sha

BASE = "b290a344a9f6e264530bb4aa39c2b37cfc9901ad"


def read(path):
    return json.loads(path.read_text())


def validate(rows):
    groups = {}
    for row in rows:
        value, usage = row["result"], row["usage"]
        summary = value["summary"]
        assert summary["admitted"] == row["jobs"] == summary["counts"]["admitted"]
        assert value["fingerprint"]["digest"] and len(value["fingerprint"]["digest"]) == 64
        assert row["provider"]["provider_api"] == 2
        assert "site-packages/simulatte_native/_bin/" in value["origin"]
        assert row["coordinator_pid"] == usage["parent_pid"]
        assert usage["samples"] > 0 and usage["tree_peak_rss"] >= usage["parent_peak_rss"] > 0
        assert len({d["pid"] for d in row["diagnostics"]}) == len(row["diagnostics"]) >= 1
        assert all(d["pid"] != row["coordinator_pid"] for d in row["diagnostics"])
        count = summary["counts"]
        if row["retention"] == "bounded":
            assert summary["schema"] == "native-production-bounded-v1"
            assert count["forgotten"] == count["completed"]
            assert count["retained_jobs"] == row["jobs"] - count["completed"]
            if row["spacing"] >= 1:
                assert count["retained_jobs"] < 256
                assert max(d["storage"]["native_bytes"] for d in row["diagnostics"] if d["storage"]) < 256000
        else:
            assert summary["schema"] == "native-production-v1"
            assert count["forgotten"] == 0 and count["retained_jobs"] == row["jobs"]
        groups.setdefault((row["jobs"], row["seed"], row["spacing"]), []).append(row)
        assert len(row["parent_checkpoints"]) == 1 + len(row["repetitions"])
        if row["repetitions"]:
            assert row["parent_checkpoints"][-1] - row["parent_checkpoints"][0] < 16 * 2**20
        for repeated in row["repetitions"]:
            assert repeated["attempt"] != value["attempt"]
            for field in ("fingerprint", "summary", "artifact_sha256"):
                assert repeated[field] == value[field]
    paired = 0
    for members in groups.values():
        base = members[0]["result"]
        for row in members[1:]:
            result = row["result"]
            assert result["fingerprint"]["kpis"] == base["fingerprint"]["kpis"]
            assert result["summary"]["input_sha256"] == base["summary"]["input_sha256"]
            assert result["summary"]["counts"]["events"] == base["summary"]["counts"]["events"]
        if {r["retention"] for r in members} == {"full", "bounded"}:
            paired += 1
        for mode in ("full", "bounded"):
            variants = [r for r in members if r["retention"] == mode]
            if len(variants) > 1:
                for row in variants[1:]:
                    for field in ("fingerprint", "summary", "artifact_sha256"):
                        assert row["result"][field] == variants[0]["result"][field]
    assert paired > 0
    return paired


def function(source, name):
    match = re.search(r"^static [^\n]+\b" + name + r"\([^\n]+\) \{", source, re.M)
    assert match, name
    pos = source.index("{", match.start())
    depth = 1
    end = pos + 1
    while depth:
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        end += 1
    return source[match.start() : end]


def audit(output, plot=False):
    meta, cert = read(output / "metadata.json"), read(output / "verification.json")
    assert meta["source"] == sources()
    assert meta["source_sha256"] == cert["source_sha256"]
    for name, expected in cert["files"].items():
        assert sha(output / name) == expected, name
    gate = read(output / "gate.json")
    assert len(gate["rows"]) == 16 and len(gate["reproductions"]) == 8 and gate["sources_deleted"]
    for original in gate["rows"]:
        for repeated in gate["reproductions"]:
            if original["request"] == repeated["request"]:
                assert repeated["attempt"] != original["attempt"]
                for field in ("fingerprint", "summary", "artifact_sha256"):
                    assert repeated[field] == original[field]
    assert read(output / "corruption.json")["published_results"] == 0
    rows = read(output / "rows.json")
    paired = validate(rows)
    # Check independent producer hashes, including the named seed derivation.
    wanted = {}
    for row in rows:
        wanted.setdefault((row["seed"], row["spacing"]), set()).add(row["jobs"])
    expected = {}
    for (seed, spacing), sizes in wanted.items():
        with Environment(seed=seed) as env:
            producer = Producer(env.rng("retention-input").getrandbits(32), spacing)
        h = hashlib.sha256()
        while producer.cursor < max(sizes):
            h.update(encoded(producer.next()))
            if producer.cursor in sizes:
                expected[producer.cursor, seed, spacing] = h.hexdigest()
    for row in rows:
        assert row["result"]["summary"]["input_sha256"] == expected[row["jobs"], row["seed"], row["spacing"]]
    # Prior research and raw evidence stay immutable; integration source changes are intentional.
    preserved = 0
    raw = subprocess.check_output(["git", "ls-tree", "-rz", BASE, "--", "prototypes/", "specs/research/"], cwd=REPO)
    for record in raw.split(b"\0"):
        if not record:
            continue
        fields, name = record.split(b"\t", 1)
        mode, _, blob = fields.split()
        path = REPO / name.decode()
        data = os.readlink(path).encode() if mode == b"120000" else path.read_bytes()
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == blob.decode(), name
        preserved += 1
    original = (REPO / "prototypes/native_engine_dynamic/csrc/dynamic_engine.c").read_text()
    current = (REPO / "native/csrc/dynamic_engine.c").read_text()
    names = (
        "process",
        "grant",
        "enqueue",
        "release_pending",
        "pool_before",
        "pool_push",
        "pool_pop",
        "floor_change",
        "wip_change",
        "before",
        "schedule",
        "pop",
    )
    for name in names:
        assert function(original, name) == function(current, name), name
    assert (
        (REPO / "native/csrc/stock_kpi.c")
        .read_bytes()
        .startswith((REPO / "prototypes/native_engine_dynamic/csrc/stock_kpi.c").read_bytes())
    )
    assert not any('.c"' in p.read_text() for p in (REPO / "native/csrc").glob("*.c"))
    rejected = 0
    for field in ("input", "count"):
        bad = copy.deepcopy(rows)
        if field == "input":
            bad[0]["result"]["summary"]["input_sha256"] = "0" * 64
        else:
            bad[0]["result"]["summary"]["counts"]["admitted"] = -1
        try:
            validate(bad)
        except AssertionError:
            rejected += 1
        else:
            raise AssertionError("Corrupt evidence accepted")
    table = []
    stable = [r for r in rows if r["spacing"] == 1.5 and not r["extra_queries"] and not r["repetitions"]]
    for n in sorted({r["jobs"] for r in stable}):
        entry = dict(jobs=n)
        for mode in ("full", "bounded"):
            selected = [r for r in stable if r["jobs"] == n and r["retention"] == mode]
            entry[mode] = dict(
                parent_mib=statistics.median(r["usage"]["parent_peak_rss"] for r in selected) / 2**20,
                tree_mib=statistics.median(r["usage"]["tree_peak_rss"] for r in selected) / 2**20,
                child_self_mib=statistics.median(max(d["peak_rss"] for d in r["diagnostics"]) for r in selected)
                / 2**20,
                wall_seconds=statistics.median(r["usage"]["wall_seconds"] for r in selected),
                trace_bytes=statistics.median(r["result"]["trace_bytes"] for r in selected),
                store_bytes=statistics.median(r["store_bytes"] for r in selected),
            )
        table.append(entry)
    if len(table) >= 3:
        assert max(e["bounded"]["tree_mib"] for e in table) - min(e["bounded"]["tree_mib"] for e in table) < 32
        assert max(e["bounded"]["parent_mib"] for e in table) - min(e["bounded"]["parent_mib"] for e in table) < 16
        assert table[-1]["full"]["child_self_mib"] > table[-1]["bounded"]["child_self_mib"] + 64
    result = dict(
        passed=True,
        source_sha256=meta["source_sha256"],
        gate_runs=16,
        source_deleted_reproductions=8,
        memory_cases=len(rows),
        paired_scenarios=paired,
        regenerated_inputs=len(expected),
        preserved_historical_files=preserved,
        unchanged_transition_functions=len(names),
        deliberate_corruptions_rejected=rejected,
        stable_summary=table,
    )
    if plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), constrained_layout=True)
        for mode, color in (("full", "#b34d3b"), ("bounded", "#237994")):
            x = [r["jobs"] for r in table]
            axes[0].plot(x, [r[mode]["tree_mib"] for r in table], "o-", color=color, label=mode)
            axes[1].plot(x, [r[mode]["parent_mib"] for r in table], "o-", color=color, label=mode)
        for ax in axes:
            ax.set_xscale("log")
            ax.set_xlabel("Submitted jobs")
            ax.grid(alpha=0.2)
            ax.legend()
        axes[0].set_ylabel("Coordinator tree sampled peak RSS (MiB)")
        axes[1].set_ylabel("Coordinator parent sampled peak RSS (MiB)")
        fig.suptitle("Installed provider · fresh Coordinator executions · CPython/macOS")
        fig.savefig(output / "memory.png", dpi=180)
        plt.close(fig)
    (output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--plot", action="store_true")
    args = p.parse_args()
    audit(args.output, args.plot)
