"""Publication exposes only a fenced, verified and durably indexed result."""

from dataclasses import asdict, replace
import hashlib
import importlib
import importlib.util
import os

import pytest

from simulatte import Environment, Trace, TraceRecorder
from simulatte.execution import schema
from simulatte.execution.protocol import MAX_FRAGMENT
from simulatte.execution.receipts import SegmentReceiver
from tests.execution.test_receipts import descriptor
from tests.execution.test_store import api as store_api
from tests.execution.test_schema import request
from simulatte.stopping import Horizon, execute_until


def api():
    assert importlib.util.find_spec("simulatte.execution.publication"), "result publication missing"
    return importlib.import_module("simulatte.execution.publication")


def prepared(tmp_path, *, level="full", transform=None, build=None, finalize=True, managed=True, wrong_identity=False):
    m = api()
    store = store_api().RunStore.open(tmp_path / ".simulatte")
    requested = request(schema, seed=42, level=level, parameters={}, kpis=(), stopping=Horizon(1).to_dict())
    key = store.assign(requested, store.create_revision({"source": "a"}))
    env = Environment(seed=42)
    path = tmp_path / "source.simtrace"
    resolved = schema.ResolvedManifest(runtime={}, dependencies={}, imports={}, layout={}, capabilities={})
    embedded = replace(requested, layout_revision=requested.layout_revision + 1) if wrong_identity else requested
    recorder = TraceRecorder(
        env,
        path,
        level=level,
        **(
            {
                "execution": {"schema": 1, "request_id": schema.request_id(embedded), "request": embedded.to_dict()},
                "resolve_execution": resolved.to_dict,
            }
            if managed
            else {}
        ),
    )
    if build is not None:
        build(env)
    execute_until(env, Horizon(1))
    recorder.close(termination="horizon")
    data = path.read_bytes()
    if transform is not None:
        data = transform(data)
        path.write_bytes(data)
    trace = Trace.open(path)
    assert trace.fingerprint is not None
    receiver = SegmentReceiver(store, key)
    desc = descriptor(data)
    for offset in range(0, len(data), MAX_FRAGMENT):
        receiver.receive(desc, offset, data[offset : offset + MAX_FRAGMENT])
    for status in ("preparing", "ready", "running", "finalizing") if finalize else ("preparing", "ready", "running"):
        store.transition(key, status)
    proposal = m.ResultProposal(
        trace_sha256=hashlib.sha256(data).hexdigest(),
        simulation_manifest=dict(trace.manifest),
        resolved_manifest=schema.ResolvedManifest(runtime={}, dependencies={}, imports={}, layout={}, capabilities={}),
        fingerprint=asdict(trace.fingerprint),
        artifacts=(),
    )
    return store, key, proposal, receiver


@pytest.mark.parametrize("level", ["full", "kpi"])
def test_verified_result_becomes_visible_only_after_index_commit(tmp_path, level):
    m = api()
    store, key, proposal, receiver = prepared(tmp_path, level=level)
    with store:
        stages = []

        def observe(stage):
            stages.append(stage)
            with store._transaction() as db:
                count = db.execute("SELECT count(*) FROM results").fetchone()[0]
            assert count == int(stage == "committed")

        result = m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True, checkpoint=observe)
        assert result.key == key
        assert result.path == store.root / "results" / key.attempt_id
        assert Trace.open(result.path / "run.simtrace").check() is None
        assert store.attempt(key)["status"] == "succeeded"
        assert stages == ["verified", "staged", "renamed", "before_commit", "committed"]
        assert store.result(key.attempt_id).path == result.path
    with type(store).open(store.root) as reopened:
        assert reopened.result(key.attempt_id).path == result.path
        assert result.path.exists()


@pytest.mark.parametrize("exited,cleaned", [(False, False), (True, False), (False, True)])
def test_publication_requires_execution_exit_and_cleanup(tmp_path, exited, cleaned):
    m = api()
    store, key, proposal, _ = prepared(tmp_path)
    with store:
        with pytest.raises(ValueError, match="cleanup"):
            m.publish(store, key, proposal, execution_exited=exited, descendants_cleaned=cleaned)
        with pytest.raises(KeyError):
            store.result(key.attempt_id)


@pytest.mark.parametrize("changed", ["trace", "fingerprint", "manifest"])
def test_proposal_mismatch_never_publishes(tmp_path, changed):
    m = api()
    store, key, proposal, _ = prepared(tmp_path)
    if changed == "trace":
        proposal = replace(proposal, trace_sha256="0" * 64)
    elif changed == "fingerprint":
        proposal = replace(proposal, fingerprint={"digest": "0" * 64, "kpis": {}})
    else:
        proposal = replace(proposal, simulation_manifest={})
    with store:
        with pytest.raises(ValueError, match="mismatch"):
            m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        with pytest.raises(KeyError):
            store.result(key.attempt_id)


@pytest.mark.parametrize("stage", ["verified", "staged", "renamed", "before_commit"])
def test_revoke_during_finalization_cannot_publish(tmp_path, stage):
    m = api()
    store, key, proposal, _ = prepared(tmp_path)
    with store:

        def revoke(at):
            if at == stage:
                store.revoke(key, "cancelled")

        with pytest.raises(ValueError):
            m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True, checkpoint=revoke)
        with pytest.raises(KeyError):
            store.result(key.attempt_id)
    with type(store).open(store.root) as reopened:
        assert not (reopened.root / "results" / key.attempt_id).exists()
        assert reopened.attempt(key)["status"] == "cancelled"


def test_crc_valid_but_semantically_wrong_trace_is_rejected(tmp_path):
    from simulatte._wire import FrozenMap
    from tests.core.test_trace_reader import _rewrite_footer

    def corrupt(data):
        def replace_digest(body):
            body["fingerprint"] = FrozenMap({**body["fingerprint"], "digest": "0" * 64})

        return _rewrite_footer(data, replace_digest)

    m = api()
    store, key, proposal, _ = prepared(tmp_path, transform=corrupt)
    with store:
        with pytest.raises(ValueError, match="semantic"):
            m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        with pytest.raises(KeyError):
            store.result(key.attempt_id)


def test_artifacts_are_assembled_and_indexed_including_empty_files(tmp_path):
    m = api()
    store, key, proposal, receiver = prepared(tmp_path, finalize=False)
    with store:
        data = b"output bytes"
        receiver.receive(descriptor(data, id="output", sequence=2, stream="artifact/data/output.txt"), 0, data)
        proposal = replace(
            proposal,
            artifacts=(
                {"path": "data/output.txt", "length": len(data), "sha256": hashlib.sha256(data).hexdigest()},
                {"path": "empty.txt", "length": 0, "sha256": hashlib.sha256(b"").hexdigest()},
            ),
        )
        store.transition(key, "finalizing")
        result = m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        assert (result.path / "artifacts/data/output.txt").read_bytes() == data
        assert (result.path / "artifacts/empty.txt").read_bytes() == b""
        with store._transaction() as db:
            assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 2


@pytest.mark.parametrize(
    "stage", ["verified", "staged", "renamed", "before_commit", "in_transaction", "committed", "rename_before_sync"]
)
@pytest.mark.parametrize("crash", ["exit", *(["sigkill"] if os.name == "posix" else [])])
def test_process_crash_never_adopts_an_unindexed_result(tmp_path, stage, crash):
    import json
    import subprocess
    import sys

    script = """
from pathlib import Path
from dataclasses import asdict
import json,os,signal,sys
from tests.execution.test_publication import prepared,api
root=Path(sys.argv[1]); stage=sys.argv[2]
def crash():
    if sys.argv[3] == "sigkill": os.kill(os.getpid(), signal.SIGKILL)
    os._exit(83)
store,key,proposal,_=prepared(root)
(root/'key.json').write_text(json.dumps(asdict(key)))
m=api()
def checkpoint(at):
    if at==stage: crash()
    if at=='before_commit' and stage=='in_transaction':
        class CrashDB:
            def __getattr__(self,name): return getattr(self.connection,name)
            def execute(self,statement,*args):
                if statement=='COMMIT': crash()
                return self.connection.execute(statement,*args)
        wrapper=CrashDB(); wrapper.connection=store._db; store._db=wrapper
if stage=='rename_before_sync':
    def move(source,destination):
        os.replace(source,destination); crash()
    m.replace_durable=move
m.publish(store,key,proposal,execution_exited=True,descendants_cleaned=True,checkpoint=checkpoint)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), stage, crash], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == (-9 if crash == "sigkill" else 83), result.stderr
    key = schema.AttemptKey(**json.loads((tmp_path / "key.json").read_text()))
    with store_api().RunStore.open(tmp_path / ".simulatte") as store:
        if stage == "committed":
            published = store.result(key.attempt_id)
            assert Trace.open(published.path / "run.simtrace").verify() is True
            assert store.attempt(key)["status"] == "succeeded"
        else:
            with pytest.raises(KeyError):
                store.result(key.attempt_id)
            assert store.attempt(key)["status"] == "lost"
            assert not (store.root / "results" / key.attempt_id).exists()


def test_artifact_copy_rejects_symlink_and_change_during_read(tmp_path):
    import io

    m = api()
    source = tmp_path / "source"
    source.write_bytes(b"abc")
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="regular"):
        m._copy(link, io.BytesIO(), hashlib.sha256())

    class MutatingOutput:
        def write(self, data):
            source.write_bytes(b"changed")
            return len(data)

    with pytest.raises(ValueError, match="changed during"):
        m._copy(source, MutatingOutput(), hashlib.sha256())


@pytest.mark.parametrize(
    "artifact",
    [
        {"path": "../outside", "length": 0, "sha256": "0" * 64},
        {"path": "ok", "length": -1, "sha256": "0" * 64},
        {"path": "ok", "length": True, "sha256": "0" * 64},
        {"path": "ok", "length": 1},
    ],
)
def test_invalid_artifact_proposals_fail_before_publication(tmp_path, artifact):
    store, _, proposal, _ = prepared(tmp_path)
    with store:
        with pytest.raises(ValueError):
            replace(proposal, artifacts=(artifact,))


def test_casefold_colliding_artifact_names_are_rejected(tmp_path):
    store, _, proposal, _ = prepared(tmp_path)
    with store:
        with pytest.raises(ValueError, match="colliding"):
            replace(
                proposal,
                artifacts=(
                    {"path": "Data", "length": 0, "sha256": "0" * 64},
                    {"path": "data", "length": 0, "sha256": "0" * 64},
                ),
            )


@pytest.mark.parametrize("failure", ["staging", "rename", "index"])
def test_disk_failures_never_index_a_result_and_recover(tmp_path, monkeypatch, failure):
    import errno
    import sqlite3

    m = api()
    store, key, proposal, _ = prepared(tmp_path)
    with store:

        def full(*a, **k):
            raise OSError(errno.ENOSPC, "disk full")

        if failure == "staging":
            monkeypatch.setattr(m, "_sync", full)
        elif failure == "rename":
            monkeypatch.setattr(m, "replace_durable", full)
        else:

            class FailingDB:
                def __init__(self, db):
                    self.db = db

                def __getattr__(self, name):
                    return getattr(self.db, name)

                def execute(self, statement, *args):
                    if statement.startswith("INSERT INTO results"):
                        raise sqlite3.OperationalError("database or disk is full")
                    return self.db.execute(statement, *args)

            monkeypatch.setattr(store, "_db", FailingDB(store._db))
        with pytest.raises((OSError, sqlite3.OperationalError)):
            m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        with pytest.raises(KeyError):
            store.result(key.attempt_id)
    with type(store).open(store.root) as reopened:
        assert not (reopened.root / "results" / key.attempt_id).exists()
        assert reopened.attempt(key)["status"] == "lost"


@pytest.mark.parametrize(
    "failure",
    ["missing", "undeclared", "segment_hash", "segment_length", "wrong_request", "outcome", "gap", "sequence_gap"],
)
def test_invalid_staged_result_fails_verification(tmp_path, failure):
    from tests.core.test_trace_reader import _rewrite_footer

    m = api()
    transform = (
        (lambda data: _rewrite_footer(data, lambda body: body.update(outcome="cancelled")))
        if failure == "outcome"
        else None
    )
    store, key, proposal, receiver = prepared(tmp_path, transform=transform, finalize=failure != "undeclared")
    with store:
        if failure == "missing":
            proposal = replace(proposal, artifacts=({"path": "missing", "length": 1, "sha256": "0" * 64},))
        elif failure == "undeclared":
            receiver.receive(descriptor(b"x", id="extra", sequence=2, stream="artifact/extra"), 0, b"x")
        elif failure in ("segment_hash", "segment_length", "gap", "sequence_gap"):
            with store._transaction() as db:
                column, value = {
                    "segment_hash": ("sha256", "0" * 64),
                    "segment_length": ("length", 1),
                    "gap": ("offset", 1),
                    "sequence_gap": ("sequence", 2),
                }[failure]
                db.execute(f"UPDATE receipts SET {column}=?", (value,))
        elif failure == "wrong_request":
            with store._transaction() as db:
                from simulatte.execution.store import _blob

                # Corrupt persisted requested bytes independently of the child's proposal.
                row = db.execute("SELECT body FROM requests").fetchone()
                from simulatte.execution.store import _read

                body = _read(row[0])
                body["seed"] = "43"
                db.execute("UPDATE requests SET body=?", (_blob(body),))
        if failure == "undeclared":
            store.transition(key, "finalizing")
        with pytest.raises(ValueError):
            m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        with pytest.raises(KeyError):
            store.result(key.attempt_id)


def test_kpi_scalars_are_indexed_with_the_verified_result(tmp_path):
    from tests.core.test_kpi import _desk_system
    from simulatte.execution.store import _read

    m = api()
    store, key, proposal, _ = prepared(tmp_path, build=lambda env: _desk_system(env, jobs=[(0.0, 0.5)]))
    with store:
        m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        with store._transaction() as db:
            scalars = {row[0]: _read(row[1]) for row in db.execute("SELECT name,value FROM kpis")}
        assert scalars["desk/completed"] == 1.0
        assert scalars["desk/flow_time"] == 0.5


@pytest.mark.parametrize("failure", ["phase", "upload", "no_trace", "staging_exists", "result_exists"])
def test_incomplete_or_preexisting_publication_is_refused(tmp_path, failure):
    m = api()
    store, key, proposal, receiver = prepared(tmp_path, finalize=failure != "upload")
    with store:
        if failure == "phase":
            with store._transaction() as db:
                db.execute("UPDATE attempts SET status='running'")
        elif failure == "upload":
            data = b"x" * (MAX_FRAGMENT + 1)
            receiver.receive(
                descriptor(data, id="output", sequence=2, stream="artifact/output"), 0, data[:MAX_FRAGMENT]
            )
        elif failure == "no_trace":
            with store._transaction() as db:
                db.execute("DELETE FROM receipts")
        elif failure == "staging_exists":
            (receiver.root / "publish").mkdir()
        else:
            (store.root / "results" / key.attempt_id).mkdir()
        if failure == "upload":
            store.transition(key, "finalizing")
        with pytest.raises(ValueError):
            m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        with pytest.raises(KeyError):
            store.result(key.attempt_id)


def test_trace_policy_cannot_disagree_with_execution_request(tmp_path):
    from simulatte._wire import freeze
    from tests.core.test_trace_reader import _rewrite_footer

    def change_policy(data):
        def update(body):
            body["manifest"] = freeze(
                {**body["manifest"], "stopping_policy": {"type": "horizon", "horizon": 2.0, "warmup": 0.0}}
            )

        return _rewrite_footer(data, update)

    m = api()
    store, key, proposal, _ = prepared(tmp_path, transform=change_policy)
    with store:
        with pytest.raises(ValueError, match="requested stopping_policy"):
            m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)


def test_artifact_short_copy_is_not_successful(tmp_path):
    from types import SimpleNamespace

    m = api()
    path = tmp_path / "data"
    path.write_bytes(b"abc")
    with pytest.raises(OSError, match="Incomplete"):
        m._copy(path, SimpleNamespace(write=lambda data: 0), hashlib.sha256())


@pytest.mark.parametrize("when", ["before", "after"])
def test_artifact_replacement_around_open_is_detected(tmp_path, monkeypatch, when):
    import io
    from pathlib import Path
    from contextlib import contextmanager

    m = api()
    source = tmp_path / "source"
    source.write_bytes(b"abc")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"abc")
    original = Path.open

    @contextmanager
    def swapped(path, *args, **kwargs):
        if when == "before":
            replacement.replace(source)
        with original(path, *args, **kwargs) as file:
            yield file
        if when == "after":
            replacement.replace(source)

    monkeypatch.setattr(Path, "open", swapped)
    with pytest.raises(ValueError, match="changed " + when):
        m._copy(source, io.BytesIO(), hashlib.sha256())


def test_publication_reads_many_receipts_in_bounded_batches(tmp_path):
    m = api()
    store, key, proposal, receiver = prepared(tmp_path, finalize=False)
    with store:
        for offset in range(130):
            receiver.receive(
                descriptor(b"x", id=f"part-{offset}", stream="artifact/many.txt", sequence=offset + 2, offset=offset),
                0,
                b"x",
            )
        data = b"x" * 130
        proposal = replace(
            proposal, artifacts=({"path": "many.txt", "length": len(data), "sha256": hashlib.sha256(data).hexdigest()},)
        )
        store.transition(key, "finalizing")
        result = m.publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        assert (result.path / "artifacts/many.txt").read_bytes() == data


@pytest.mark.parametrize("failure", ["missing", "request", "resolved", "termination"])
def test_execution_provenance_is_bound_to_published_result(tmp_path, failure):
    from tests.core.test_trace_reader import _rewrite_footer

    transform = (
        (
            lambda data: _rewrite_footer(
                data, lambda body: body.update(execution={**body["execution"], "termination": "failed"})
            )
        )
        if failure == "termination"
        else None
    )
    store, key, proposal, _ = prepared(
        tmp_path, managed=failure != "missing", wrong_identity=failure == "request", transform=transform
    )
    with store:
        if failure == "resolved":
            proposal = replace(
                proposal, resolved_manifest=replace(proposal.resolved_manifest, runtime={"different": True})
            )
        with pytest.raises(ValueError, match="execution"):
            api().publish(store, key, proposal, execution_exited=True, descendants_cleaned=True)
        with pytest.raises(KeyError):
            store.result(key.attempt_id)
