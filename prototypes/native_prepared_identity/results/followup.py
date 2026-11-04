"""Three fresh paired repeats of the fixed/seed102 dense timing outlier."""

import hashlib
import json
import os
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from prototypes.native_prepared_identity.common import sources
from prototypes.native_retention_integration.run import monitor

ROOT = Path(__file__).resolve().parent
meta = json.loads((ROOT / "live-final/metadata.json").read_text())
workspace = Path(meta["workspace"])
out = ROOT / "live-followup"
out.mkdir(exist_ok=False)
baseline = next(
    r
    for r in json.loads((ROOT / "live-final/rows.json").read_text())
    if r["fixture"] == "fixed"
    and r["ticks"] == 32768
    and r["seed"] == 102
    and r["query_every"] == 1
    and r["projection_mode"] == "unprepared"
)
rng, rows = random.Random(20261105), []
for pair in range(3):
    modes = ["hook", "unprepared"]
    rng.shuffle(modes)
    for mode in modes:
        assert sources() == meta["source"]
        name = f"pair-{pair}-{mode}"
        work = workspace / ("followup-" + name)
        work.mkdir()
        diag, identities = work / "diagnostics", work / "identities"
        diag.mkdir()
        identities.mkdir()
        env = dict(
            os.environ,
            PATH=str(workspace / "runtime-path"),
            CC="/no-runtime-compiler",
            SIMULATTE_PROJECTION_MODE=mode,
            SIMULATTE_IDENTITIES_QUERY_EVERY="1",
            SIMULATTE_IDENTITIES_DISABLE="0",
            SIMULATTE_FIXED_DIAGNOSTICS=str(diag),
            SIMULATTE_FEEDBACK_DIAGNOSTICS=str(diag),
            SIMULATTE_IDENTITY_DIAGNOSTICS=str(identities),
        )
        usage = monitor(
            [
                workspace / "venv/bin/python",
                "-I",
                workspace / "probe.py",
                work / "result.json",
                "--evidence",
                out / "proofs",
                "--case",
                "--fixture",
                "fixed",
                "--ticks",
                "32768",
                "--seed",
                "102",
            ],
            env,
            out / (name + ".log"),
            cwd=workspace,
        )
        value = json.loads((work / "result.json").read_text())
        for field in ("fingerprint", "physical", "reporting", "native", "identities", "artifact_sha256"):
            assert value["result"][field] == baseline["result"][field], field
        value.update(pair=pair, projection_mode=mode, usage=usage)
        rows.append(value)
        (out / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")
        print(name, usage["wall_seconds"], flush=True)
(out / "index.json").write_text(json.dumps(rows, indent=2) + "\n")
cert = dict(
    source_sha256=meta["source_sha256"],
    runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()},
)
(out / "verification.json").write_text(json.dumps(cert, indent=2) + "\n")
