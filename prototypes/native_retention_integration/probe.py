"""Installed-only Coordinator probe; copied outside checkout by the gate runner."""

import argparse
import hashlib
from importlib import metadata
import json
import os
import platform
from pathlib import Path
import shutil
import sys

from simulatte import Trace
from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.slots import SlotConfig
from simulatte.execution.schema import _plain
from simulatte.execution.snapshot import discover_project
from simulatte.experiments import Experiment, Explicit, Replications, run
from simulatte.stopping import Horizon
import simulatte_native

MODEL = """from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import resource
import sys
from simulatte import model
from simulatte.experimental.native import NativeProduction
from producer import Producer, encoded

@dataclass(frozen=True)
class Params:
    jobs: int = 96
    retention: str = "bounded"
    dynamic: bool = True
    policy: str = "spt"
    spacing: float = 0.25
    window: int = 8
    observe: bool = False
    corrupt: bool = False

@model(params=Params, horizon=20)
def build(env, layout, params):
    producer = Producer(env.rng("retention-input").getrandbits(32), params.spacing)
    inputs = hashlib.sha256()
    def input_state():
        return dict(producer.state(), limit=params.jobs, window=params.window)

    class ReportingBackend(NativeProduction):
        def finish(self):
            counts = self.storage_stats()
            super().finish()
            # Deterministic output only; physical allocation/polling is diagnostic.
            content = dict(input_sha256=inputs.hexdigest(), admitted=producer.cursor, producer=producer.state(),
                backend_state=self.state_digest(), schema=self.capabilities.state_schema,
                counts={k: counts[k] for k in ("admitted", "completed", "forgotten",
                    "events", "retained_jobs", "retained_operations")})
            (env.artifacts_dir / "native-summary.json").write_text(json.dumps(content, sort_keys=True))

        def close(self):
            try:
                stats = self._stream.storage_stats() if self._stream is not None else None
            except RuntimeError:
                stats = None
            try:
                super().close()
            finally:
                directory = os.environ.get("SIMULATTE_NATIVE_MEMORY_DIR")
                if directory:
                    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    if sys.platform != "darwin": peak *= 1024
                    Path(directory, str(os.getpid()) + ".json").write_text(json.dumps(dict(
                        pid=os.getpid(), peak_rss=peak, storage=stats, cursor=producer.cursor)))

    work = []
    if not params.dynamic:
        for _ in range(params.jobs):
            job = producer.next(); inputs.update(encoded(job)); work.append(job)
    backend = ReportingBackend(3, work, retention=params.retention,
        wip_cap=12, policy=params.policy, input_state=input_state if params.dynamic else None)
    env.attach_execution_backend(backend, scope="shopfloor")
    del work
    if params.corrupt:
        provider = importlib.import_module("simulatte_native")
        extension = next((Path(provider.__file__).parent / "_bin").glob("_nd_scalar.*"))
        with extension.open("ab") as file: file.write(b"retention-corruption-probe")
    if params.dynamic:
        def arrivals():
            while producer.cursor < params.jobs:
                for _ in range(min(params.window, params.jobs - producer.cursor)):
                    job = producer.next(); inputs.update(encoded(job)); backend.add(job)
                if params.observe or os.environ.get("SIMULATTE_NATIVE_QUERY_EXTRA") == "1":
                    backend.kpi_values(); backend.state_digest(); backend.storage_stats()
                yield env.timeout(producer.cursor * params.spacing - env.now)
        env.process(arrivals())
"""


def project(root):
    root.mkdir()
    (root / "plant.py").write_text(MODEL)
    shutil.copyfile(Path(__file__).with_name("producer.py"), root / "producer.py")
    (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude=["plant.py","producer.py"]\n')
    return discover_project(root / "plant.py", root)


def result_row(result, *, config=None, seed=None):
    trace = Trace.open(result.path / "run.simtrace")
    trace.check()
    assert trace.fingerprint.digest == result.fingerprint["digest"]
    imports = result.manifest["resolved"]["imports"]
    origin = imports["simulatte_native._nd_scalar"]
    assert origin.startswith("external:") and "site-packages/simulatte_native/_bin/" in origin
    assert not any(name.startswith("prototypes") for name in imports)
    output = json.loads((result.path / "artifacts/native-summary.json").read_text())
    return dict(
        configuration=config,
        seed=seed,
        fingerprint=_plain(result.fingerprint),
        attempt=result.key.attempt_id,
        request=result.key.request_id,
        origin=origin,
        summary=output,
        trace_bytes=(result.path / "run.simtrace").stat().st_size,
        artifact_sha256=hashlib.sha256((result.path / "artifacts/native-summary.json").read_bytes()).hexdigest(),
    )


def gate(args):
    root = args.output.parent / "project"
    spec = project(root)
    definitions = [
        dict(label=f"{mode}-{policy}-{dynamic}", parameters=dict(retention=mode, policy=policy, dynamic=dynamic))
        for mode in ("full", "bounded")
        for policy in ("fifo", "spt")
        for dynamic in (False, True)
    ]
    definition = Experiment(
        name="native retention install",
        model="plant.py:build",
        stopping=Horizon(10, warmup=2),
        configurations=Explicit(definitions),
        replications=Replications(count=2, base_seed=100),
        level="kpi",
    )
    status = run(definition, project=root, slots=SlotConfig(local=2), timeout=60)
    assert status["counts"]["succeeded"] == 16 and not status["counts"].get("reused", 0)
    rows, selected = [], []
    with Coordinator(spec) as coordinator:
        for item in coordinator.store.replications(status["run_id"]):
            row = result_row(coordinator.store.result(item["attempt"]), config=item["configuration"], seed=item["seed"])
            rows.append(row)
            if item["index"] == 0:
                selected.append(row)
    # The eight configurations differ only in execution/storage, so physical KPIs agree by policy/seed.
    for policy in range(2):
        for seed in (100, 101):
            group = [r for r in rows if r["configuration"] % 4 // 2 == policy and int(r["seed"]) == seed]
            assert len(group) == 4 and all(r["fingerprint"]["kpis"] == group[0]["fingerprint"]["kpis"] for r in group)
    for name in ("plant.py", "producer.py"):
        (root / name).unlink()
    reproduced = []
    with Coordinator.from_store(root, selected[0]["request"]) as coordinator:
        for original in selected:
            repeated = result_row(coordinator.reproduce(original["request"], timeout=60))
            assert repeated["fingerprint"] == original["fingerprint"]
            assert repeated["summary"] == original["summary"]
            assert repeated["artifact_sha256"] == original["artifact_sha256"]
            assert repeated["attempt"] != original["attempt"]
            reproduced.append(repeated)
    return dict(rows=rows, reproductions=reproduced, sources_deleted=True)


def case(args):
    root = args.output.parent / "project"
    spec = project(root)
    parameters = dict(
        jobs=args.jobs, retention=args.retention, spacing=args.spacing, window=64, dynamic=True, observe=args.observe
    )
    with Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=40)
        request = coordinator.make_request(
            prepared,
            parameters=parameters,
            seed=args.seed,
            stopping=Horizon(args.jobs * args.spacing, warmup=100).to_dict(),
            level="kpi",
        )
        result = coordinator.wait(coordinator.submit(request, prepared.revision, timeout=180), timeout=200)
        observed = result_row(result)
    import resource

    def parent_peak():
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value if sys.platform == "darwin" else value * 1024

    parent_checkpoints = [parent_peak()]
    repetitions = []
    if args.reproduce_count:
        for name in ("plant.py", "producer.py"):
            (root / name).unlink()
        with Coordinator.from_store(root, observed["request"]) as coordinator:
            for _ in range(args.reproduce_count):
                repeated = result_row(coordinator.reproduce(observed["request"], timeout=180))
                assert repeated["fingerprint"] == observed["fingerprint"]
                assert repeated["summary"] == observed["summary"]
                assert repeated["artifact_sha256"] == observed["artifact_sha256"]
                assert repeated["attempt"] != observed["attempt"]
                repetitions.append(repeated)
                parent_checkpoints.append(parent_peak())
    store_bytes = sum(p.stat().st_size for p in (root / ".simulatte").rglob("*") if p.is_file())
    return dict(
        retention=args.retention,
        jobs=args.jobs,
        seed=args.seed,
        spacing=args.spacing,
        observe=args.observe,
        result=observed,
        store_bytes=store_bytes,
        repetitions=repetitions,
        parent_checkpoints=parent_checkpoints,
    )


def corruption(args):
    root = args.output.parent / "project"
    spec = project(root)
    with Coordinator(spec) as coordinator:
        prepared = coordinator.prepare(timeout=40)
        request = coordinator.make_request(
            prepared, parameters=dict(corrupt=True, retention="bounded"), stopping=Horizon(10).to_dict(), level="kpi"
        )
        try:
            coordinator.wait(coordinator.submit(request, prepared.revision, timeout=40), timeout=50)
        except ExecutionFailure as error:
            failure = str(error)
        else:
            raise AssertionError("Mutated provider published a result")
        with coordinator.store._transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
    return dict(rejected=True, published_results=0, error=failure)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--case", action="store_true")
    p.add_argument("--corrupt", action="store_true")
    p.add_argument("--jobs", type=int, default=16384)
    p.add_argument("--retention", choices=["full", "bounded"], default="bounded")
    p.add_argument("--spacing", type=float, default=1.5)
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--observe", action="store_true")
    p.add_argument("--reproduce-count", type=int, default=0)
    args = p.parse_args()
    assert shutil.which("cc") is None and shutil.which("clang") is None
    assert simulatte_native.info()["provider_api"] == 2
    value = corruption(args) if args.corrupt else case(args) if args.case else gate(args)
    assert not any(name.startswith("prototypes") for name in sys.modules)
    value.update(
        runtime=dict(python=sys.version, platform=platform.platform(), machine=platform.machine()),
        coordinator_pid=os.getpid(),
        provider=None if args.corrupt else simulatte_native.info(),
        distributions={d.metadata["Name"]: d.version for d in metadata.distributions()},
    )
    args.output.write_text(json.dumps(value, indent=2) + "\n")
