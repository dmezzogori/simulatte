"""Replayable evidence audit and standalone memory figure; no timing work here."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess

from .build import REPO, ROOT, sources
from .producer import Producer, encoded
from .stream import verify

BASE = "9ecc2629b0db0fa67f1e39efa7e48d60dfff81fa"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key(row):
    return row["jobs"], row["seed"], row["spacing"], row["horizon"], row["execution"]


def validate_rows(rows):
    pairs, semantic = {}, {}
    for row in rows:
        n = row["jobs"]
        assert n > 0 and row["window"] == 64
        assert row["retention"] in ("full", "bounded")
        stats = row["final"]
        assert stats["admitted"] == n and 0 <= stats["completed"] <= n
        assert row["max_retained_jobs"] >= stats["retained_jobs"]
        assert row["max_retained_operations"] >= stats["retained_operations"]
        if row["retention"] == "bounded":
            assert stats["forgotten"] == stats["completed"]
            assert stats["retained_jobs"] == n - stats["completed"]
        else:
            assert stats["forgotten"] == 0 and stats["retained_jobs"] == n
        if not row["horizon"]:
            assert stats["completed"] == n
        if row["execution"] == "environment":
            assert row["environment"]["entities"] == row["environment"]["collectors"] == 1
            assert row["environment"]["pending_events"] <= 2
            assert type(row["environment"]["fingerprint"]) is str
            assert len(row["environment"]["fingerprint"]) == 64
        pair = pairs.setdefault(key(row), {})
        assert row["retention"] not in pair
        pair[row["retention"]] = row
        value = row["input_sha256"], row["snapshot"], row["history_sha256"], row["state_sha256"]
        group = key(row)[:-1]
        assert semantic.setdefault(group, value) == value, group
        assert row["samples"][0]["submitted"] == 0
        assert row["samples"][-1]["submitted"] == n
        assert 2 <= len(row["samples"]) <= 12
        if row["spacing"] >= 1 and row["retention"] == "bounded":
            assert row["max_retained_jobs"] <= 256
            assert row["max_retained_operations"] <= 768
            assert row["max_native_bytes"] <= 256000
            assert max(sample["state_bytes"] for sample in row["samples"]) <= 65536
        assert all(row[field] >= 0 for field in ("create_seconds", "loop_seconds", "finish_seconds", "close_seconds"))
    assert all(set(pair) == {"full", "bounded"} for pair in pairs.values())
    for pair in pairs.values():
        if pair["full"]["environment"] is not None:
            assert pair["full"]["environment"] == pair["bounded"]["environment"]
    overload = [r for r in rows if r["spacing"] < 1 and r["retention"] == "bounded"]
    if overload:
        assert max(r["final"]["retained_jobs"] for r in overload) > 1000
    return pairs


def audit(output, *, plot=False):
    metadata = json.loads((output / "metadata.json").read_text())
    assert len(metadata["base"]) == 40 and all(c in "0123456789abcdef" for c in metadata["base"])
    assert subprocess.run(["git", "merge-base", "--is-ancestor", BASE, metadata["base"]], cwd=REPO).returncode == 0
    assert metadata["source"] == sources()
    assert metadata["build_manifest"] == verify()
    certificate = json.loads((output / "verification.json").read_text())
    assert certificate["source_sha256"] == metadata["source_sha256"]
    for name, digest in certificate["files"].items():
        assert sha(output / name) == digest, name
    rows = json.loads((output / "rows.json").read_text())
    pairs = validate_rows(rows)
    # Independently regenerate input hashes once per seed/spacing, retaining no jobs.
    groups = {}
    for row in rows:
        groups.setdefault((row["seed"], row["spacing"]), set()).add(row["jobs"])
    inputs = {}
    for (seed, spacing), sizes in groups.items():
        producer, digest = Producer(seed, spacing), hashlib.sha256()
        while producer.cursor < max(sizes):
            digest.update(encoded(producer.next()))
            if producer.cursor in sizes:
                inputs[producer.cursor, seed, spacing] = digest.hexdigest()
    for row in rows:
        assert inputs[row["jobs"], row["seed"], row["spacing"]] == row["input_sha256"]
    # Every previously tracked file, including raw evidence and public API, is immutable.
    previous = subprocess.check_output(["git", "ls-tree", "-rz", BASE], cwd=REPO)
    preserved = 0
    for record in previous.split(b"\0"):
        if not record:
            continue
        fields, name = record.split(b"\t", 1)
        mode, kind, blob = fields.split()
        assert kind == b"blob"
        path = REPO / name.decode()
        data = os.readlink(path).encode() if mode == b"120000" else path.read_bytes()
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == blob.decode(), name
        preserved += 1
    # Deliberate evidence corruption must not survive the independent comparisons.
    rejected = []
    mutations = [
        lambda r: r[0].update(input_sha256="0" * 64),
        lambda r: r[0].update(history_sha256="0" * 64),
        lambda r: r[0].update(state_sha256="0" * 64),
        lambda r: r[0]["final"].update(admitted=-1),
        lambda r: r[0]["snapshot"].update(window=[-1, -1, True]),
    ]
    for i, mutate in enumerate(mutations):
        bad = copy.deepcopy(rows)
        mutate(bad)
        try:
            validate_rows(bad)
        except AssertionError:
            rejected.append(i)
        else:
            raise AssertionError("corrupted evidence accepted")
    stable = [r for r in rows if r["spacing"] == 1.5 and not r["horizon"]]
    table = []
    for n in sorted({r["jobs"] for r in stable}):
        modes = {}
        for mode in ("full", "bounded"):
            selected = [r for r in stable if r["jobs"] == n and r["retention"] == mode]
            modes[mode] = dict(
                peak_rss_mib=statistics.median(r["peak_rss"] for r in selected) / 2**20,
                loop_seconds=statistics.median(r["loop_seconds"] for r in selected),
                loop_seconds_range=[min(r["loop_seconds"] for r in selected), max(r["loop_seconds"] for r in selected)],
                state_query_ms=statistics.median(r["samples"][-2]["query_seconds"] for r in selected) * 1000,
                max_retained_jobs=max(r["max_retained_jobs"] for r in selected),
                max_native_bytes=max(r["max_native_bytes"] for r in selected),
            )
        table.append(dict(jobs=n, **modes))
    if len(table) >= 3:
        assert max(r["bounded"]["peak_rss_mib"] for r in table) - min(r["bounded"]["peak_rss_mib"] for r in table) <= 8
        assert table[-1]["full"]["peak_rss_mib"] > table[-1]["bounded"]["peak_rss_mib"] + 64
    result = dict(
        passed=True,
        cases=len(rows),
        paired_cases=len(pairs),
        regenerated_inputs=len(inputs),
        source_sha256=metadata["source_sha256"],
        preserved_base_files=preserved,
        deliberate_corruptions_rejected=len(rejected),
        exact_retention_and_environment_parity=True,
        stable_summary=table,
    )
    if plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), constrained_layout=True)
        for mode, color in (("full", "#b34d3b"), ("bounded", "#237994")):
            x = [r["jobs"] for r in table]
            axes[0].plot(x, [r[mode]["peak_rss_mib"] for r in table], "o-", color=color, label=mode)
            axes[1].plot(x, [r[mode]["loop_seconds"] for r in table], "o-", color=color, label=mode)
        for ax in axes:
            ax.set_xscale("log")
            ax.set_xlabel("Submitted jobs")
            ax.grid(alpha=0.2)
            ax.legend()
        axes[0].set_ylabel("Whole-process peak RSS (MiB)")
        axes[1].set_ylabel("Input + native execution loop (s)")
        fig.suptitle("Research retention experiment · CPython/macOS · median of 3 cold processes")
        fig.savefig(output / "memory.png", dpi=180)
        plt.close(fig)
    (output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path, nargs="?", default=ROOT / "results/gate")
    p.add_argument("--plot", action="store_true")
    a = p.parse_args()
    audit(a.output, plot=a.plot)
