"""Same stock observation contract with and without the final trace writer."""

import argparse
import itertools
import json
from pathlib import Path
import platform
import tempfile

from prototypes.native_engine.benchmark import file_hash
from .observation_gate import case
from .production_benchmark import source_hash, sources
from .scalar_benchmark import libraries
from .semantics_gate import workloads
from .simultaneous import quantized


def configurations():
    return list(
        itertools.product(
            [*workloads(), *[f"wide-{i}" for i in range(8)]],
            (1, 2, 12),
            ("fifo", "spt"),
            ("independent", "feedback"),
            ("none", "full"),
            (0, 2),
            (None, 4),
        )
    )


def check_rows(rows):
    assert [
        (r["case"], r["cap"], r["policy"], r["producer"], r["level"], r["warmup"], r["horizon"]) for r in rows
    ] == configurations()
    grouped = {}
    for row in rows:
        assert row["final_sha256"][0] == row["final_sha256"][1]
        assert row["probe_sha256"][0] == row["probe_sha256"][1]
        key = tuple(row[k] for k in ("case", "cap", "policy", "producer", "warmup", "horizon"))
        previous = grouped.setdefault(key, row)
        for field in ("trajectory", "final_sha256", "probe_sha256", "checkpoints", "events", "scalars"):
            assert row[field] == previous[field], (key, field)


def gate(output):
    output.mkdir(parents=True, exist_ok=False)
    frozen, binaries = source_hash(), libraries()
    metadata = dict(
        source_sha256=frozen, files=sources(), libraries=binaries, interpreter=platform.python_implementation()
    )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    templates = {**workloads(), **{f"wide-{i}": quantized(i, jobs=24) for i in range(8)}}
    rows = []
    with tempfile.TemporaryDirectory(prefix="untraced-observers-") as tmp, (output / "rows.jsonl").open("w") as target:
        for name, cap, policy, producer, level, warmup, horizon in configurations():
            result = case(
                templates[name],
                Path(tmp),
                cap=cap,
                policy=policy,
                producer=producer,
                level=level,
                warmup=warmup,
                horizon=horizon,
            )
            row = dict(
                case=name,
                cap=cap,
                policy=policy,
                producer=producer,
                level=level,
                warmup=warmup,
                horizon=horizon,
                **result,
            )
            rows.append(row)
            target.write(json.dumps(row) + "\n")
    check_rows(rows)
    assert source_hash() == frozen and libraries() == binaries
    result = dict(
        source_sha256=frozen,
        configurations=len(rows),
        model_runs=2 * len(rows),
        native_production_values_and_probes_exact=True,
        recording_values_and_probes_exact=True,
        evidence={name: file_hash(output / name) for name in ("metadata.json", "rows.jsonl")},
    )
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    gate(parser.parse_args().output)
