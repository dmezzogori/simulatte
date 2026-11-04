"""Fresh installed helper/oracle and source-deleted managed reproduction gate."""

import hashlib
import importlib
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys

from simulatte import Environment
from simulatte._wire import canonical_pack
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.schema import _plain
from simulatte.execution.snapshot import discover_project
from simulatte.experimental.native import NativeProduction
from simulatte.experimental.native_identity import compose_native_identity
from simulatte.stopping import Horizon, execute_until


def digest(data):
    return hashlib.sha256(data).hexdigest()


def artifacts(directory):
    return {p.name: digest(p.read_bytes()) for p in directory.iterdir() if p.is_file()}


def main(output, fixtures):
    assert shutil.which("cc") is None and shutil.which("clang") is None
    assert "site-packages" in str(importlib.import_module("simulatte.experimental.native_identity").__file__)
    sys.path.insert(0, str(fixtures))
    plant = importlib.import_module("plant")
    projection = importlib.import_module("projection")
    prepared_projection = importlib.import_module("prepared_projection")

    # Exercise the real provider in both encodings, including callback-only changes.
    queries = []
    for retention in ("full", "bounded"):
        calls, state = [], dict(seed=1, future=[dict(id=0, due=4.0)])

        def inputs():
            calls.append(None)
            return state

        with Environment() as env:
            env._execution_policy = Horizon(10)
            env.configure_kpis()
            backend = NativeProduction(1, retention=retention, input_state=inputs)
            env.attach_execution_backend(backend, scope="shopfloor")
            env.activate()
            actions = projection.Actions().state()
            calls.clear()
            view = backend.state_projection()
            assert len(calls) == 2
            first = compose_native_identity(view, actions=actions)
            assert len(calls) == 2  # Composition does not query the callback again.
            assert first.canonical_bytes == canonical_pack(prepared_projection.prepare(view, actions))
            for oracle in (projection.prepared.hook_capture, projection.unprepared.hook_capture):
                original, sizes = oracle(backend, actions)
                assert first.canonical_bytes == projection.reference.pack(original)
                assert sizes == dict(raw_bytes=first.native_bytes, view_bytes=first.size_bytes)
            original, _ = projection.reference.capture(backend, inputs, actions)
            assert first.canonical_bytes == projection.reference.pack(original)
            projection.validate(first.to_dict())
            state["seed"] += 1
            second = compose_native_identity(backend.state_projection(), actions=actions).to_dict()
            assert second["trajectory_control"] == first.to_dict()["trajectory_control"]
            assert second["reporting"] == first.to_dict()["reporting"]
            assert second["authority"] != first.to_dict()["authority"]
            assert second["reproduction_state"] != first.to_dict()["reproduction_state"]
            projection.validate(second)
            # Both original input and independently returned dictionary are detached.
            view["authority_preimage"]["inputs"]["future"].clear()
            detached = first.to_dict()
            detached["authority"]["digest"] = "bad"
            assert first.canonical_bytes == projection.reference.pack(first.to_dict())
            assert state["future"] == [dict(id=0, due=4.0)]
            queries.append(
                dict(
                    retention=retention,
                    canonical_sha256=digest(first.canonical_bytes),
                    native_bytes=first.native_bytes,
                    view_bytes=first.size_bytes,
                )
            )

    # The frozen model/validator files are identical to the preceding experiment.
    os.environ["SIMULATTE_IDENTITIES_QUERY_EVERY"] = "1"
    direct = []
    for fixture in ("fixed", "coupled"):
        for retention in ("full", "bounded"):
            expected = None
            for mode in ("helper", "prepared", "unprepared", "reference"):
                os.environ["SIMULATTE_PROJECTION_MODE"] = mode
                directory = output.parent / f"direct-{fixture}-{retention}-{mode}"
                directory.mkdir()
                with Environment(seed=100, artifacts_dir=directory) as env:
                    policy = Horizon(96 if fixture == "fixed" else 80, warmup=10)
                    env._execution_policy = policy
                    env.configure_kpis(warmup=10)
                    env.enable_digest()
                    plant.build(env, None, plant.Params(fixture=fixture, engine=retention))
                    execute_until(env, policy)
                    fingerprint = asdict(env.fingerprint())
                row = dict(
                    fixture=fixture,
                    retention=retention,
                    mode=mode,
                    fingerprint=fingerprint,
                    artifacts=artifacts(directory),
                )
                physical = (fingerprint, row["artifacts"])
                if expected is None:
                    expected = physical
                assert expected == physical, (fixture, retention, mode)
                direct.append(row)

    os.environ["SIMULATTE_PROJECTION_MODE"] = "helper"
    project = output.parent / "identity-project"
    shutil.copytree(fixtures, project)
    (project / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["*.py"]\n')
    rows = []
    spec = discover_project(project / "plant.py", project)
    with Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=40)
        for fixture in ("fixed", "coupled"):
            for retention in ("full", "bounded"):
                request = coordinator.make_request(
                    prepared,
                    seed=100,
                    parameters=dict(fixture=fixture, engine=retention),
                    stopping=Horizon(96 if fixture == "fixed" else 80, warmup=10).to_dict(),
                    level="kpi",
                )
                result = coordinator.wait(coordinator.submit(request, prepared.revision, timeout=40), timeout=50)
                files = artifacts(result.path / "artifacts")
                baseline = next(
                    r
                    for r in direct
                    if r["fixture"] == fixture and r["retention"] == retention and r["mode"] == "helper"
                )
                assert files == baseline["artifacts"], (files, baseline["artifacts"])
                imports = result.manifest["resolved"]["imports"]
                assert not any(name.startswith("prototypes") for name in imports)
                helper_origin = imports["simulatte.experimental.native_identity"]
                assert helper_origin.endswith("simulatte/experimental/native_identity.py"), helper_origin
                rows.append(
                    dict(
                        fixture=fixture,
                        retention=retention,
                        fingerprint=_plain(result.fingerprint),
                        artifacts=files,
                        request=result.key.request_id,
                        attempt=result.key.attempt_id,
                        helper_origin=helper_origin,
                    )
                )
    for path in project.glob("*.py"):
        path.unlink()
    reproduced = []
    with Coordinator.from_store(project, rows[0]["request"]) as coordinator:
        for row in rows:
            result = coordinator.reproduce(row["request"], timeout=50)
            assert _plain(result.fingerprint) == row["fingerprint"]
            assert artifacts(result.path / "artifacts") == row["artifacts"]
            assert result.key.attempt_id != row["attempt"]
            reproduced.append(dict(request=row["request"], attempt=result.key.attempt_id))
    assert not any(name.startswith("prototypes") for name in sys.modules)
    output.write_text(
        json.dumps(
            dict(
                passed=True,
                python=sys.version,
                queries=queries,
                direct=direct,
                managed=rows,
                source_deleted_reproductions=reproduced,
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
