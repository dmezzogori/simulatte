"""Installed-only live-model and controlled-history query profiler."""

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulatte import Environment
from simulatte.experimental.native import Job, NativeProduction
from simulatte.stopping import Horizon, execute_until
import simulatte
import simulatte_native

if __package__:
    from . import measurement, plant, projection
    from . import reference_projection as reference
else:
    import measurement
    import plant
    import projection
    import reference_projection as reference


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def simulate(root, args, *, profiled):
    root.mkdir()
    artifacts = root / "artifacts"
    artifacts.mkdir()
    queries = []
    with Environment(seed=args.seed, artifacts_dir=artifacts) as env:
        env.enable_digest()
        if args.kind == "live":
            until = 96 if args.fixture == "fixed" else 80
            policy = Horizon(until, warmup=10)
            env._execution_policy = policy
            env.configure_kpis(warmup=10)
            case = plant.fixed_case if args.fixture == "fixed" else plant.coupled_case
            original, controllers = case.Controller, []

            class Captured(original):
                def __init__(self, *values):
                    super().__init__(*values)
                    controllers.append(self)

            case.Controller = Captured
            try:
                plant.build(env, None, plant.Params(fixture=args.fixture, engine=args.retention, policy=args.policy))
            finally:
                case.Controller = original
            controller = controllers[0]
            backend, inputs, actions = controller.adapter, controller.state, controller.actions
            sample_at = 65.0
        else:
            policy = Horizon(args.jobs + 1, warmup=10)
            env._execution_policy = policy
            env.configure_kpis(warmup=10)
            cursor = 0
            padding = [
                dict(
                    id=i,
                    route=[0, 1, 2],
                    durations=[0.25, 0.5, 0.75],
                    due=float(i + 10),
                    priority=i % 7,
                    family="synthetic",
                    ready=bool(i % 2),
                )
                for i in range(args.padding)
            ]

            def synthetic_inputs():
                return dict(
                    schema="projection-profile-source-v1",
                    seed=args.seed,
                    cursor=cursor,
                    total=args.jobs,
                    next_at=float(cursor),
                    padding=padding,
                )

            inputs = synthetic_inputs
            backend = NativeProduction(1, retention=args.retention, wip_cap=4, policy=args.policy, input_state=inputs)
            env.attach_execution_backend(backend, scope="shopfloor")
            actions = reference.Actions()

            def source():
                nonlocal cursor
                for i in range(args.jobs):
                    at = float(env.now)
                    due = at + 2 + args.seed % 3
                    backend.add(Job(at, (0,), (0.25,), due))
                    actions.update(at, i, 4, [dict(id=i, arrival=at, route=(0,), durations=(0.25,), due=due)])
                    cursor += 1
                    yield plant.fixed_case.wake(env, float(cursor))

            env.process(source())
            sample_at = args.jobs + 0.5

            def finish():
                identity, _ = projection.hook_capture(backend, actions.state())
                reference.validate(identity)
                (artifacts / "identities.json").write_text(json.dumps(identity, sort_keys=True) + "\n")
                (artifacts / "native.json").write_text(json.dumps(identity["authority"], sort_keys=True) + "\n")

            plant.fixed_case.wake(env, policy.until, priority=-1, callback=finish)

        def inspect():
            if profiled:
                result = measurement.measure(
                    backend,
                    inputs,
                    actions.state(),
                    root / "profiles",
                    seed=args.seed,
                    blocks=args.blocks,
                    target_ns=args.target_ns,
                )
                queries.append(result)

        plant.fixed_case.wake(env, sample_at, priority=-1, callback=inspect)
        execute_until(env, policy)
        fingerprint = asdict(env.fingerprint())
    return dict(
        fingerprint=fingerprint,
        artifacts={p.name: sha(p) for p in sorted(artifacts.iterdir()) if p.is_file()},
        queries=queries,
    )


def main(args):
    assert "site-packages" in simulatte.__file__
    assert "site-packages" in simulatte_native.__file__
    assert not any(k.startswith("prototypes") for k in sys.modules)
    base = simulate(args.output / "baseline", args, profiled=False)
    profiled = simulate(args.output / "profiled", args, profiled=True)
    assert base["fingerprint"] == profiled["fingerprint"] and base["artifacts"] == profiled["artifacts"]
    assert len(profiled["queries"]) == 1
    module = sys.modules["simulatte_native._nd_scalar"]
    assert "site-packages/simulatte_native/_bin/" in module.__file__
    value = dict(
        parameters=dict(
            kind=args.kind,
            fixture=args.fixture,
            retention=args.retention,
            seed=args.seed,
            policy=args.policy,
            jobs=args.jobs,
            padding=args.padding,
        ),
        baseline={k: v for k, v in base.items() if k != "queries"},
        profiled={k: v for k, v in profiled.items() if k != "queries"},
        query=profiled["queries"][0],
        pid=os.getpid(),
        provider=simulatte_native.info(),
        origin=module.__file__,
        runtime=dict(python=sys.version, platform=platform.platform()),
    )
    (args.output / "result.json").write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--kind", choices=("live", "history", "callback"), default="live")
    p.add_argument("--fixture", default="fixed")
    p.add_argument("--retention", default="bounded")
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--policy", default="spt")
    p.add_argument("--jobs", type=int, default=128)
    p.add_argument("--padding", type=int, default=0)
    p.add_argument("--blocks", type=int, default=5)
    p.add_argument("--target-ns", type=int, default=25_000_000)
    arguments = p.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=False)
    main(arguments)
