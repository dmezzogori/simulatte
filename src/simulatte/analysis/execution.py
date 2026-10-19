"""One independent local analysis slot, with contained one-use children."""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
import os
from pathlib import Path
import threading
import time

from simulatte.execution.process import ContainedProcess
from simulatte.execution.snapshot import ProjectSpec, load_source, _extract
from simulatte.execution.store import _remove
from simulatte.projects.environments import EnvironmentPool
from . import codec
from .context import write_input
from .publication import publish
from .store import AnalysisStore

# What the child reads per replication; resolved manifests, requests and fingerprints
# stay in the store under the snapshot hash recorded in the output provenance.
CHILD_FIELDS = (
    "run",
    "configuration",
    "index",
    "seed",
    "status",
    "reason",
    "attempt",
    "available",
    "request_id",
    "scalars",
    "datasets",
    "artifacts",
)


def child_inputs(snapshot):
    return {
        **snapshot,
        "replications": [{k: row[k] for k in CHILD_FIELDS if k in row} for row in snapshot["replications"]],
    }


class AnalysisRunner:
    def __init__(self, store, *, environments=None, timeout=300):
        self.store, self.analysis = store, AnalysisStore(store)
        self.environments = environments or EnvironmentPool(store.root.parent, store)
        self.timeout = timeout
        self._lock = threading.RLock()
        self._futures, self._processes = {}, {}
        self._closed = False
        with store._mutex:
            if getattr(store, "_analysis_runner", None) is not None:
                raise ValueError("The store already owns its analysis slot")
            store._analysis_runner = self
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simulatte-analysis")

    def submit(self, execution_id):
        with self._lock:
            if self._closed:
                raise ValueError("Analysis runner is closed")
            if execution_id in self._futures:
                return
            if self.analysis.execution(execution_id).state != "waiting":
                raise ValueError("Only a waiting execution can be submitted")
            future = self._executor.submit(self._run, execution_id)
            self._futures[execution_id] = future
            future.add_done_callback(lambda _: self._finished(execution_id))

    def _finished(self, execution_id):
        with self._lock:
            self._futures.pop(execution_id, None)

    def cancel(self, execution_id):
        result = self.analysis.cancel(execution_id)
        with self._lock:
            child = self._processes.get(execution_id)
            if child is not None:
                child.terminate()
        return result

    def wait(self, execution_id, timeout=None):
        with self._lock:
            future = self._futures.get(execution_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.analysis.execution(execution_id)

    def _transition(self, identity, before, after):
        with self.store._transaction() as db:
            return (
                db.execute(
                    "UPDATE analysis_executions SET state=? WHERE id=? AND state=?", (after, identity, before)
                ).rowcount
                == 1
            )

    def _record(self, identity, details, *, failed=False):
        """Merge ``details``; ``failed`` also fails a running or finalizing execution."""
        with self.store._transaction() as db:
            row = db.execute("SELECT state,details FROM analysis_executions WHERE id=?", (identity,)).fetchone()
            # A cancelled execution may be deleted while its child is still being stopped.
            if row is None or failed and row[0] not in ("running", "finalizing"):
                return
            state, saved = row
            db.execute(
                "UPDATE analysis_executions SET state=?,details=? WHERE id=?",
                ("failed" if failed else state, codec.dumps({**codec.loads(saved), **details}), identity),
            )

    def _source(self, definition, destination):
        project = ProjectSpec(
            self.store.root.parent,
            definition["entrypoint"].partition(":")[0],
            "layout.json",
            (),
            (),
            tuple(definition.get("capture", {}).get("python_path", (".",))),
            False,
        )
        source = load_source(project, definition["source_hash"])
        destination.mkdir()
        _extract(source, destination)
        for path in destination.rglob("*"):
            path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
        destination.chmod(0o555)
        return project

    def _inputs(self, snapshot, destination):
        from simulatte.execution.publication import _copy
        import hashlib

        destination.mkdir()
        seen = set()
        for row in snapshot["replications"]:
            if not row["available"] or row["attempt"] in seen:
                continue
            seen.add(row["attempt"])
            needed = {
                str(Path(dataset["path"]) / chunk["path"])
                for dataset in row["datasets"].values()
                for chunk in dataset["manifest"]["chunks"]
            }
            for descriptor in row["artifacts"]:
                if descriptor["path"] not in needed:
                    continue
                source = self.store.root / "results" / row["attempt"] / "artifacts" / descriptor["path"]
                target = destination / row["attempt"] / descriptor["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                # The published descriptor is the sole authority for input bytes.
                digest = hashlib.sha256()
                with target.open("xb") as stream:
                    length = _copy(source, stream, digest)
                    stream.flush()
                    os.fsync(stream.fileno())
                if length != descriptor["length"] or digest.hexdigest() != descriptor["sha256"]:
                    raise ValueError("Captured input hash/length mismatch")
                target.chmod(0o444)
        for path in sorted((p for p in destination.rglob("*") if p.is_dir()), reverse=True):
            path.chmod(0o555)
        destination.chmod(0o555)

    def _control_path(self, identity):
        return self.store.root / "analysis-control" / (identity + ".cancel")

    def _control(self, identity):
        path = self._control_path(identity)
        # Only a regular file requests cancellation; links and directories are absent.
        if path.is_file() and not path.is_symlink():
            # The store also discards the request while cancelling.
            self.analysis.cancel(identity)
            path.unlink(missing_ok=True)

    def _run(self, identity):
        directory = self.store.root / "analysis-staging" / identity
        child, acquired, drains = None, False, []
        diagnostics = bytearray()
        try:
            self._control(identity)
            if not self._transition(identity, "waiting", "running"):
                return
            item = self.analysis.execution(identity)
            generation = self.environments.acquire(item.generation_id, "analysis-" + identity)
            acquired = True
            if self.analysis.execution(identity).state != "running":
                return
            definition = self.analysis.definition(item.definition_id)
            snapshot = self.analysis.snapshot(item.snapshot_hash)
            with self.store._transaction() as db:
                parameters = codec.loads(
                    db.execute("SELECT parameters FROM analysis_executions WHERE id=?", (identity,)).fetchone()[0]
                )
            directory.mkdir()
            project = self._source(definition, directory / "source")
            self._inputs(snapshot, directory / "inputs")
            provenance = {
                "definition_revision": item.definition_id,
                "source_hash": definition["source_hash"],
                "environment_generation": generation.id,
                "environment": generation.evidence,
                "input_snapshot": item.snapshot_hash,
                "parameters": parameters,
            }
            config = {
                "source": str(directory / "source"),
                "python_path": project.python_path,
                "project_root": str(project.root),
                "entrypoint": definition["entrypoint"],
                "inputs": child_inputs(snapshot),
                "input_dir": str(directory / "inputs"),
                "output": str(directory / "output"),
                "execution_id": identity,
                "parameters": parameters,
                "requirements": definition["requirements"],
                "provenance": provenance,
            }
            write_input(directory / "config.json", config)
            child = ContainedProcess.start(
                [str(generation.interpreter), "-I", "-m", "simulatte.analysis.child", str(directory / "config.json")],
                cwd=directory,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )

            def drain(stream):
                while block := stream.read(65536):
                    with self._lock:
                        diagnostics.extend(block)
                        del diagnostics[:-8192]

            drains = [
                threading.Thread(target=drain, args=(stream,), daemon=True) for stream in (child.stdout, child.stderr)
            ]
            for thread in drains:
                thread.start()
            with self._lock:
                self._processes[identity] = child
            deadline, cancelled_at = time.monotonic() + self.timeout, None
            while child.returncode is None:
                self._control(identity)
                if self.analysis.execution(identity).state == "cancelled" or time.monotonic() >= deadline:
                    if cancelled_at is None:
                        cancelled_at = time.monotonic()
                        child.terminate()
                    elif time.monotonic() - cancelled_at > 0.5:
                        child.kill()
                try:
                    child.wait(timeout=0.05)
                except TimeoutError:
                    pass
            # Descendant cleanup is part of success, not a finally-only best effort.
            try:
                child.close()
            except (OSError, TimeoutError) as error:
                # Recorded in every state; cancellation only keeps the failure below
                # from changing the state.
                self._record(identity, {"cleanup_error": str(error)[-8192:]})
                raise
            for thread in drains:
                thread.join(5)
            if child.returncode != 0 or cancelled_at is not None:
                raise ValueError(
                    "Analysis child failed, cancelled or timed out: " + diagnostics.decode(errors="replace")
                )
            self.environments._verify(generation)
            if self._transition(identity, "running", "finalizing"):
                publish(self.store, identity, directory / "output", provenance)
        except Exception as error:
            self._record(identity, {"message": str(error)[-8192:]}, failed=True)
        finally:
            try:
                if child is not None:
                    child.close()
            except (OSError, TimeoutError) as error:
                # Cleanup failure is recorded even for cancellation; it must not
                # escape wait() or turn already-cancelled work into success.
                self._record(identity, {"cleanup_error": str(error)[-8192:]})
            finally:
                for thread in drains:
                    thread.join(5)
                with self._lock:
                    self._processes.pop(identity, None)
                if acquired:
                    self.environments.release("analysis-" + identity)
                if directory.exists():
                    _remove(directory)
                # Any request left at a terminal transition is stale; the store may
                # discard it concurrently.
                with suppress(FileNotFoundError):
                    _remove(self._control_path(identity))
                # A rename preceding an index failure is not success.
                with self.store._transaction() as db:
                    indexed = db.execute("SELECT 1 FROM analysis_outputs WHERE execution=?", (identity,)).fetchone()
                output = self.store.root / "analysis-results" / identity
                if indexed is None and output.exists():
                    _remove(output)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            identities = [identity for identity, future in self._futures.items() if not future.done()]
        for identity in identities:
            try:
                self.cancel(identity)
            except KeyError:
                # Deleted once terminal; its future only has cleanup left.
                pass
        self._executor.shutdown(wait=True)
        with self.store._mutex:
            self.store._analysis_runner = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
