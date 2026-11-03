"""Audit Linux evidence and exact shared physical results against the macOS gate."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from ..analysis import audit, read
from ..run import REPO, sha, sources

BASE = "dfbb1a1e21eb885013f44ada987c8172e469aef1"


def key(row):
    if "result" not in row:
        return row["configuration"], int(row["seed"])
    return (row["retention"], row["jobs"], row["seed"], row["spacing"], row["extra_queries"], len(row["repetitions"]))


def unpack(row):
    return row["result"] if "result" in row else row


def verify(output, plot=False):
    linux, mac = output / "gate", output.parent / "results/gate"
    result = audit(linux)
    lm, mm = read(linux / "metadata.json"), read(mac / "metadata.json")
    assert lm["base"] == BASE
    assert lm["source"] == mm["source"] == sources()
    assert lm["core"] == mm["core"] and lm["native"]["sha256"] != mm["native"]["sha256"]
    assert "Linux" in lm["host_platform"] and "macOS" in mm["host_platform"]
    checks = {phase: read(output / phase / "checks.json") for phase in ("release", "safety")}
    assert all(v["passed"] and all(c["exit_code"] == 0 for c in v["commands"]) for v in checks.values())
    junit = ET.parse(output / "release/regressions.xml").getroot()
    cases = list(junit.iter("testcase"))
    assert not list(junit.iter("failure")) and not list(junit.iter("error"))
    skipped = len(list(junit.iter("skipped")))
    native_cases = [c for c in cases if c.attrib["classname"].startswith("tests.experimental.test_native")]
    assert len(native_cases) == 83
    coverage = ET.parse(output / "release/coverage.xml").getroot().attrib
    combined = (int(coverage["lines-covered"]) + int(coverage["branches-covered"])) / (
        int(coverage["lines-valid"]) + int(coverage["branches-valid"])
    )
    assert combined >= 0.99
    for line in (
        "full allocation-failure positions: 164",
        "bounded allocation-failure positions: 156",
        "streaming ASan/UBSan checks pass",
    ):
        assert line in (output / "safety/standalone-sanitizer.log").read_text()
    assert "83 passed" in (output / "safety/boundary-tests.log").read_text()
    rejections = read(output / "release/install-check/rejections.json")
    assert len(rejections) == 7 and all(v["rejected"] for v in rejections.values())
    pairs = []
    for kind in ("gate", "memory"):
        if kind == "gate":
            left, right = (read(p / "gate.json")["rows"] for p in (linux, mac))
        else:
            left, right = (read(p / "rows.json") for p in (linux, mac))
        indexed = {key(r): unpack(r) for r in right}
        assert len(left) == len(indexed)
        for row in left:
            a, b = unpack(row), indexed[key(row)]
            assert a["fingerprint"]["kpis"] == b["fingerprint"]["kpis"], key(row)
            for name in ("input_sha256", "counts", "producer", "admitted", "schema"):
                assert a["summary"][name] == b["summary"][name], (key(row), name)
            pairs.append(
                dict(
                    kind=kind,
                    key=key(row),
                    physical_equal=True,
                    backend_state_equal=a["summary"]["backend_state"] == b["summary"]["backend_state"],
                    semantic_fingerprint_equal=a["fingerprint"] == b["fingerprint"],
                    artifact_equal=a["artifact_sha256"] == b["artifact_sha256"],
                )
            )
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
    (output / "platform-pairs.json").write_text(json.dumps(pairs, indent=2) + "\n")
    value = dict(
        passed=True,
        source_sha256=lm["source_sha256"],
        common_audit=result,
        regression=dict(
            passed=len(cases) - skipped, skipped=skipped, combined_coverage_percent=combined * 100, native_cases=83
        ),
        platform_pairs=len(pairs),
        backend_state_matches=sum(p["backend_state_equal"] for p in pairs),
        semantic_fingerprint_matches=sum(p["semantic_fingerprint_equal"] for p in pairs),
        artifact_matches=sum(p["artifact_equal"] for p in pairs),
        preserved_base_files=preserved,
        linux_scripts={
            p.relative_to(REPO).as_posix(): sha(p) for p in sorted(Path(__file__).parent.glob("*")) if p.is_file()
        },
    )
    if plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), constrained_layout=True)
        table = result["stable_summary"]
        for mode, color in (("full", "#b34d3b"), ("bounded", "#237994")):
            for ax, field in zip(axes, ("tree_mib", "child_self_mib")):
                ax.plot([r["jobs"] for r in table], [r[mode][field] for r in table], "o-", label=mode, color=color)
        for ax, label in zip(axes, ("Coordinator tree sampled peak RSS (MiB)", "Simulation child self peak RSS (MiB)")):
            ax.set_xscale("log")
            ax.set_xlabel("Submitted jobs")
            ax.set_ylabel(label)
            ax.grid(alpha=0.2)
            ax.legend()
        fig.suptitle("Installed provider · fresh Coordinator executions · CPython/Linux x86-64")
        fig.savefig(output / "memory.png", dpi=180)
        plt.close(fig)
    (output / "audit.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(value, indent=2))
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()
    verify(args.output.resolve(), args.plot)
