"""Post-measurement causal check: report warmup consumed by the feedback law."""

import argparse
import json
from pathlib import Path
import tempfile

from simulatte import Environment
from simulatte.stopping import Horizon, execute_until
from ..plant import Params, build


def run(output):
    rows = []
    with tempfile.TemporaryDirectory(prefix="feedback-warmup-check-") as temporary:
        for feedback in (True, False):
            for warmup in (0, 10):
                path = Path(temporary) / f"{feedback}-{warmup}"
                path.mkdir()
                with Environment(seed=100, artifacts_dir=path) as env:
                    policy = Horizon(80, warmup=warmup)
                    env._execution_policy = policy
                    env.configure_kpis(warmup=warmup)
                    env.enable_digest()
                    build(env, None, Params(engine="bounded", feedback=feedback, record=False))
                    execute_until(env, policy)
                state = json.loads((path / "physical.json").read_text())["controller"]
                rows.append(
                    dict(
                        feedback=feedback,
                        warmup=warmup,
                        admitted=state["cursor"],
                        jobs_sha256=state["jobs_sha256"],
                        tight_polls=state["tight"],
                    )
                )
    on = [r for r in rows if r["feedback"]]
    off = [r for r in rows if not r["feedback"]]
    assert on[0]["jobs_sha256"] != on[1]["jobs_sha256"]
    assert off[0]["jobs_sha256"] == off[1]["jobs_sha256"]
    value = dict(
        passed=True,
        method="direct public bounded backend, identical source/seed/policy/caps/horizon; warmup only varied",
        rows=rows,
    )
    output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parents[1] / "results/warmup-sensitivity.json"
    )
    run(p.parse_args().output)
