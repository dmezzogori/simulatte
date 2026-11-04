"""Profile a live controller boundary, then require a matching completed run."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sys

from simulatte import Environment
from simulatte.experimental.native_identity import compose_native_identity
from simulatte.stopping import Horizon, execute_until

sys.path.insert(0, str(Path(__file__).parent))
import measurement
import plant


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def simulate(root, args, profiled):
    root.mkdir()
    queries = []
    with Environment(seed=100, artifacts_dir=root) as env:
        policy = Horizon(max(96, args.at + 1), warmup=10)
        env._execution_policy = policy
        env.configure_kpis(warmup=10)
        env.enable_digest()
        case = plant.fixed_case if args.fixture == "fixed" else plant.coupled_case
        old, controllers = case.Controller, []

        class Captured(old):
            def __init__(self, *args):
                super().__init__(*args)
                controllers.append(self)

        case.Controller = Captured
        try:
            plant.build(env, None, plant.Params(fixture=args.fixture, engine=args.retention, record=False))
        finally:
            case.Controller = old
        controller = controllers[0]

        def observe():
            backend, inputs, actions = controller.adapter, controller.state, controller.actions.state()
            if profiled:
                result = measurement.measure(backend, inputs, actions, args.output.parent / "query", seed=20261104)
                queries.append(result["context"]["canonical_sha256"])
            else:
                value = compose_native_identity(backend.state_projection(), actions=actions)
                queries.append(hashlib.sha256(value.canonical_bytes).hexdigest())

        case.wake(env, args.at, priority=-2, callback=observe)
        execute_until(env, policy)
        fingerprint = asdict(env.fingerprint())
    return dict(
        fingerprint=fingerprint, queries=queries, artifacts={p.name: sha(p) for p in root.iterdir() if p.is_file()}
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--fixture", required=True)
    p.add_argument("--retention", required=True)
    p.add_argument("--at", type=float, required=True)
    args = p.parse_args()
    assert shutil.which("cc") is None and shutil.which("clang") is None
    baseline = simulate(args.output.parent / "baseline", args, False)
    measured = simulate(args.output.parent / "measured", args, True)
    assert baseline == measured
    args.output.write_text(
        json.dumps(
            dict(
                passed=True,
                fixture=args.fixture,
                retention=args.retention,
                at=args.at,
                baseline=baseline,
                measured=measured,
                python=sys.version,
            ),
            indent=2,
        )
        + "\n"
    )
