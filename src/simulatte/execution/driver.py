"""Managed build/activation/stepping. Imported only by a one-use execution child."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import inspect
import os
from pathlib import Path
import stat
import threading

from simulatte import Environment, Provenance, TraceRecorder
from simulatte._wire import pack, freeze
from simulatte.execution.bootstrap import load_configuration
from simulatte.execution.hashing import canonical_hash
from simulatte.execution.references import read_document
from simulatte.execution.schema import ExecutionRequest, ResolvedManifest, request_id, _plain
from simulatte.execution.snapshot import _identity, _path
from simulatte.layout import Layout
from simulatte.model import get_model_spec, construct_parameters
from simulatte.stopping import Horizon, stopping_from_dict, execute_until
from simulatte.trace.sink import SegmentSink


class _DigestSink(SegmentSink):
    def __init__(self, spool):
        super().__init__(spool)
        self.digest = hashlib.sha256()

    def write(self, data):
        count = super().write(data)
        self.digest.update(data[:count])
        return count


def metadata_segment(spool, name, data):
    """Metadata uses the same bounded, verified transfer as trace/artifact bytes."""
    payload = pack(freeze(data))
    if len(payload) > spool.max_segment_bytes:
        raise ValueError("Execution metadata exceeds segment limit")
    sink = SegmentSink(spool, stream="metadata/" + name)
    try:
        sink.write(payload)
        segment = sink.commit()
        assert segment is not None
        return segment.to_dict()
    finally:
        sink.close()


def _artifacts(root, spool):
    result = []
    names = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise ValueError("Artifact directory must not be a symlink")
        for name in sorted(files):
            spool.control.check()
            path = Path(directory) / name
            relative = _path(path.relative_to(root).as_posix())
            if relative.casefold() in names or len(names) >= 100_000:
                raise ValueError("Artifact paths collide or exceed 100000 files")
            names.add(relative.casefold())
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or not path.resolve().is_relative_to(root):
                raise ValueError("Artifact must be a regular file inside artifacts_dir")

            digest = hashlib.sha256()
            sink = SegmentSink(spool, stream="artifact/" + relative)
            try:
                with path.open("rb") as file:
                    if _identity(os.fstat(file.fileno())) != _identity(before):
                        raise ValueError("Artifact changed before reading")
                    while data := file.read(1 << 20):
                        spool.control.check()
                        sink.write(data)
                        digest.update(data)
                        # Fixed-size segments bound disk/memory even for huge outputs.
                        sink.commit()
                    if _identity(os.fstat(file.fileno())) != _identity(before):
                        raise ValueError("Artifact changed while reading")
                if _identity(path.lstat()) != _identity(before) or sink.tell() != before.st_size:
                    raise ValueError("Artifact changed after reading")
                result.append({"path": relative, "length": sink.tell(), "sha256": digest.hexdigest()})
            finally:
                sink.close()
    return sorted(result, key=lambda item: item["path"])


def execute(config, raw_request, key, control, spool, send):
    request = ExecutionRequest.from_dict(raw_request)
    if request_id(request) != key.request_id or request.model != config["reference"]:
        raise ValueError("Execution request does not match attempt/model")
    from simulatte.execution.runtime import verify_runtime

    if "runtime_ref" not in config:
        raise ValueError("Execution requires measured runtime evidence")
    actual_runtime, dependencies, warnings = verify_runtime(config, request.runtime)
    metadata, build, policy = load_configuration(config)
    expected = (
        read_document(config["expected_metadata_ref"], "model-info")
        if "expected_metadata_ref" in config
        else config["expected_metadata"]
    )
    if any(_plain(metadata[name]) != _plain(expected[name]) for name in ("models", "selected")):
        raise ValueError("Model metadata changed after inspection")
    spec = get_model_spec(build)
    parameters = construct_parameters(spec, request.parameters)
    stopping = stopping_from_dict(request.stopping)
    root = Path(config["attempt_dir"]).resolve()
    artifacts = root / "artifacts"
    artifacts.mkdir()
    env = Environment(
        seed=request.seed,
        time_unit=spec.time_unit,
        parameters=request.parameters,
        artifacts_dir=artifacts,
        provenance=Provenance(
            model=canonical_hash("model-v1", {"source": request.source_hash, "reference": request.model}),
            source=request.source_hash,
            inputs=request.input_hash,
            dependencies=canonical_hash("dependencies-v1", dependencies),
        ),
    )
    env._execution_policy = stopping
    env.configure_kpis(warmup=stopping.warmup if isinstance(stopping, Horizon) else 0)
    sink = _DigestSink(spool)
    resolved = None
    recorder = None
    control.phase = "preparing"
    stopped = threading.Event()

    def progress():
        while not stopped.is_set():
            stats = spool.stats()
            try:
                send(
                    "progress",
                    {
                        "phase": control.phase,
                        "simulation_time": float(env.now),
                        "event_count": env._seq,
                        "spool_bytes": stats["bytes"],
                        "spool_files": stats["files"],
                        "backpressure": stats["backpressure"],
                    },
                )
            except BufferError:
                pass  # Coalescible diagnostics; this never drops trace data.
            except (OSError, ValueError) as error:
                control.fail(error)
                return
            stopped.wait(0.2)

    reporter = threading.Thread(target=progress, name="simulatte-progress", daemon=True)

    def resolve():
        nonlocal resolved
        available = {f"{collector._scope_id}/{name}" for collector in env.collectors for name in collector._declared}
        if set(request.kpis) - available:
            raise ValueError("Unknown requested KPI: " + ", ".join(sorted(set(request.kpis) - available)))
        resolved = ResolvedManifest(
            runtime=actual_runtime,
            dependencies=dependencies,
            imports=policy.audit(),
            layout=layout.prepare().bundle.manifest(),
            capabilities={"fresh_process": True, "ready_barrier": True},
            warnings=warnings,
        )
        return resolved.to_dict()

    def ready():
        assert recorder is not None and resolved is not None
        recorder.wait_committed()
        reference = metadata_segment(spool, "resolved-manifest", resolved.to_dict())
        send("ready", {"resolved": reference, "trace_length": sink.tell(), "simulation_time": float(env.now)})
        control.phase = "ready"
        control.wait_ready()
        control.phase = "running"

    try:
        reporter.start()
        recorder = TraceRecorder(
            env,
            level="full" if request.level == "full" else "kpi",
            sink=sink,
            control=control,
            execution={"schema": 1, "request_id": key.request_id, "request": request.to_dict()},
            resolve_execution=resolve,
            kpis=request.kpis or None,
        )
        if "layout_ref" in config:
            from simulatte.layout.identity import layout_hash

            layer = read_document(config["layout_ref"], "layout")
            if layout_hash("file", layer) != request.layout_hash:
                raise ValueError("Layout document differs from requested identity")
            layout = Layout.from_dict(env, layer)
        else:
            layout = Layout(env, file=config.get("layout_path"))
        env._on_ready(ready)
        with env._building():
            returned = build(env, layout, parameters)
            if returned is not None:
                if inspect.iscoroutine(returned) or inspect.isgenerator(returned):
                    returned.close()
                raise ValueError("Model build must be synchronous and return None")
        stop = execute_until(env, stopping, control)
        control.phase = "finalizing"
        recorder.close(termination=stop.reason)
        env.close()
        outputs = _artifacts(artifacts, spool)
        verify_runtime(config, request.runtime)
        assert resolved is not None
        fingerprint = asdict(env.fingerprint())
        if request.kpis:
            fingerprint["kpis"] = {k: v for k, v in fingerprint["kpis"].items() if k in request.kpis}
        proposal = {
            "schema": 1,
            "trace_sha256": sink.digest.hexdigest(),
            "simulation_manifest": _plain(env.manifest().merged()),
            "resolved_manifest": resolved.to_dict(),
            "fingerprint": fingerprint,
            "artifacts": outputs,
            "final_imports": policy.audit(),
        }
        return metadata_segment(spool, "result-proposal", proposal)
    except BaseException as error:
        control.fail(error)
        # Aborting waits releases the writer even if the receiver stopped ACKing.
        if recorder is not None:
            try:
                recorder.close(outcome="failed")
            except BaseException:
                pass
        raise
    finally:
        stopped.set()
        if reporter.ident is not None:
            reporter.join(1)
        env.close()
