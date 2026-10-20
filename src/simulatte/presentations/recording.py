"""Full presentation recordings cloned from a captured study request (design 9).

A recording copies the complete captured ``ExecutionRequest`` of a committed attempt (model,
source, inputs, layout, parameters, stopping policy with its horizon and warm-up or finite
population, KPI and measurement selection, runtime) and changes only the seed and the
recording level, which is always ``full``. Current model defaults never fill anything in.
Explicit edits of ``parameters``, ``stopping``, ``measurements`` and ``kpis`` are validated
against the captured model metadata and named by ``presentation_diff``; source, input,
layout and runtime edits are a new preparation, refused here.

Only a target equal to its original apart from ``level`` is a ``replay``, labelled by
``verify_replay``. A ``new_seed`` or ``edited`` recording is a different run and is never
verified against the original, even when its seed matches.

Each recording is a ``snapshots`` row of kind ``presentation-recording`` keyed by its attempt
id, and owns ``"references"`` to its own attempt (and result once committed) and to the
original result, so pruning keeps both until ``store prune --recordings`` drops recordings
no presentation adopted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
import re
import time
from typing import Any, Literal

from simulatte.execution.coordinator import ExecutionFailure, selected_model
from simulatte.execution.hashing import canonical_hash
from simulatte.execution.references import read_document
from simulatte.execution.replay import Verification, verify_replay
from simulatte.execution.schema import AttemptKey, ExecutionRequest, Revision, measurement_selection, request_id
from simulatte.execution.store import _blob, _read
from simulatte.model import ParameterSchema, validate_parameters
from simulatte.stopping import stopping_from_dict

KIND = "presentation-recording"
OVERRIDES = frozenset(("parameters", "stopping", "measurements", "kpis"))
# Fields fixed by the captured preparation: changing them is a new preparation, not an override.
_PREPARED = frozenset(("model", "source_hash", "input_hash", "layout_hash", "layout_revision", "runtime"))
_ATTEMPT = re.compile("[a-f0-9]{32}")
_NO_METADATA = "Explicit edits of parameters or measurements need the captured model metadata"
_BUSY = "Every compatible slot is busy; record again when one is idle"

Mode = Literal["replay", "new_seed", "edited"]


@dataclass(frozen=True, slots=True)
class RecordingJob:
    key: AttemptKey  # The recording's attempt; ``key.request_id`` is the presentation request.
    original_attempt: str
    mode: Mode
    verification: Verification | None  # Only a finished ``replay`` is verified.
    diff: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class _Captured:
    attempt: str
    request: ExecutionRequest
    worker: str | None
    revision: Revision
    preparation: bytes


def _seed(seed):
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("seed must be an integer in [0, 2**63)")
    return seed


def _parameters(original, edits, metadata):
    if not isinstance(edits, Mapping):
        raise ValueError("parameters must map parameter names to values")
    merged = {**original.parameters, **edits}
    if metadata is None:
        return merged
    schema = ParameterSchema.from_dict(selected_model(metadata)["parameters"])
    validated = validate_parameters(schema, merged)
    # Only the edited values change: unedited captured values keep their exact form.
    return {**original.parameters, **{name: validated[name] for name in edits}}


def _measurements(value, metadata):
    from simulatte.studio.experiments import measurement_choice

    if value is None:
        return None
    if isinstance(value, Mapping) and set(value) == {"plan"}:
        if metadata is None:
            raise ValueError(_NO_METADATA)
        return measurement_choice(metadata, value)
    selection = measurement_selection(value)
    if metadata is not None:
        declared = measurement_choice(metadata, {"plan": selection["plan"]})
        if declared["definition_hash"] != selection["definition_hash"]:
            raise ValueError(f"Measurement selection differs from the captured declaration of {selection['plan']!r}")
    return selection


def make_presentation_request(
    original: ExecutionRequest,
    *,
    seed: int,
    overrides: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ExecutionRequest:
    """``original`` at ``seed`` and level ``full``, with validated explicit ``overrides``.

    ``metadata`` is the captured model-info document; with it parameter and measurement
    edits are checked against the captured schemas and declarations, without it only the
    request schema applies. ``{"plan": name}`` selects a declared plan with an empty selection.
    """
    _seed(seed)
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, Mapping):
        raise ValueError("Presentation overrides must be a mapping")
    prepared = sorted(overrides.keys() & _PREPARED)
    if prepared:
        raise ValueError(
            f"Edits of {', '.join(prepared)} are a new preparation, not presentation overrides; prepare the model again"
        )
    unknown = sorted(overrides.keys() - OVERRIDES)
    if unknown:
        raise ValueError(
            f"Unknown presentation override {', '.join(map(str, unknown))}; allowed: kpis, measurements, parameters, "
            "stopping (the seed is chosen separately and the level is always full)"
        )
    changes: dict[str, Any] = {}
    if "parameters" in overrides:
        changes["parameters"] = _parameters(original, overrides["parameters"], metadata)
    if "stopping" in overrides:
        if not isinstance(overrides["stopping"], Mapping):
            raise ValueError("stopping must be a stopping policy")
        changes["stopping"] = stopping_from_dict(overrides["stopping"]).to_dict()
    if "kpis" in overrides:
        if not isinstance(overrides["kpis"], (list, tuple)):
            raise ValueError("kpis must be a list of KPI keys")
        changes["kpis"] = tuple(overrides["kpis"])
    if "measurements" in overrides:
        changes["measurements"] = _measurements(overrides["measurements"], metadata)
    return replace(original, seed=seed, level="full", **changes)


def _identical(a, b) -> bool:
    # Canonical identity: 1, 1.0 and True differ in a request, so they differ here too.
    return canonical_hash("presentation-diff-v1", {"value": a}) == canonical_hash("presentation-diff-v1", {"value": b})


def presentation_diff(original: ExecutionRequest, target: ExecutionRequest) -> list[dict[str, Any]]:
    """Every serialized request field that differs, sorted; parameters are named ``parameters.<name>``."""
    before, after = original.to_dict(), target.to_dict()
    changes = []
    for field in sorted(before.keys() | after.keys()):
        if field == "parameters":
            for name in sorted(before[field].keys() | after[field].keys()):
                old, new = before[field].get(name), after[field].get(name)
                if not _identical(old, new):
                    changes.append({"field": f"parameters.{name}", "original": old, "target": new})
        elif not _identical(before.get(field), after.get(field)):
            changes.append({"field": field, "original": before.get(field), "target": after.get(field)})
    return changes


def recording_mode(original: ExecutionRequest, target: ExecutionRequest) -> Mode:
    """``replay`` only for the original's own full-level request; ``new_seed`` when only the seed differs."""
    full = replace(original, level="full")
    if request_id(target) == request_id(full):
        return "replay"
    if request_id(target) == request_id(replace(full, seed=target.seed)):
        return "new_seed"
    return "edited"


def _captured(store, attempt_id) -> _Captured:
    if type(attempt_id) is not str or _ATTEMPT.fullmatch(attempt_id) is None:
        raise ValueError("Invalid attempt id")
    with store._transaction() as db:
        row = db.execute(
            "SELECT a.request_id,a.worker,v.number,v.digest,q.body,r.attempt IS NOT NULL AS committed "
            "FROM attempts a JOIN requests q ON q.id=a.request_id JOIN revisions v ON v.number=a.revision "
            "LEFT JOIN results r ON r.attempt=a.id WHERE a.id=?",
            (attempt_id,),
        ).fetchone()
        prepared = (
            None
            if row is None
            else db.execute(
                "SELECT manifest FROM snapshots WHERE id=? AND kind='preparation'", (row["request_id"],)
            ).fetchone()
        )
    if row is None:
        raise KeyError(attempt_id)
    if not row["committed"]:
        raise ValueError("No committed result to record from")
    if prepared is None:
        raise ValueError("Captured preparation is unavailable for this request")
    request = ExecutionRequest.from_dict(_read(row["body"]))
    return _Captured(attempt_id, request, row["worker"], Revision(row["number"], row["digest"]), prepared[0])


def _metadata(store, preparation):
    reference = _read(preparation)["documents"].get("expected_metadata_ref")
    if reference is None:
        raise ValueError(_NO_METADATA)
    path = (Path(store.root) / "documents" / (reference["sha256"] + ".msgpack")).resolve()
    return read_document({**reference, "path": str(path)}, "model-info")


def _plan(store, original_attempt, seed, overrides):
    captured = _captured(store, original_attempt)
    metadata = None
    if isinstance(overrides, Mapping) and overrides.keys() & {"parameters", "measurements"}:
        metadata = _metadata(store, captured.preparation)
    return captured, make_presentation_request(captured.request, seed=seed, overrides=overrides, metadata=metadata)


def preview_recording(store, original_attempt: str, *, seed: int, overrides=None) -> dict[str, Any]:
    """``{mode, diff, target_request_id}`` of a recording, shown before anything runs."""
    captured, target = _plan(store, original_attempt, seed, overrides)
    return {
        "mode": recording_mode(captured.request, target),
        "diff": presentation_diff(captured.request, target),
        "target_request_id": request_id(target),
    }


def _announce(coordinator, job, status, verified):
    body = {
        "attempt_id": job.key.attempt_id,
        "original_attempt": job.original_attempt,
        "mode": job.mode,
        "status": status,
        "verified": verified,
    }
    coordinator._emit("recording_state", job.key, body)


def start_recording(
    coordinator, original_attempt: str, *, seed: int, overrides=None, worker: str | None = None, timeout=None
) -> RecordingJob:
    """Schedule a recording as ``Coordinator.replay`` schedules a replay, without waiting for it.

    The captured preparation is copied for the new request, which runs at the original's
    pinned revision on an idle slot of the original host (or of ``worker``; ``"local"`` is
    the local pool). An interactive manual run keeps its slot: the interactive fallback
    then counts as busy. A runtime that differs from this installation is refused, never
    reconstructed.
    """
    coordinator._deadline(timeout)
    if worker is not None and type(worker) is not str:
        raise ValueError("worker must be a worker name")
    store = coordinator.store
    captured, target = _plan(store, original_attempt, seed, overrides)
    mode, diff = recording_mode(captured.request, target), tuple(presentation_diff(captured.request, target))
    slots = coordinator._replay_slots(captured.worker, worker)
    snapshot, documents = coordinator._pinned_preparation(captured.preparation, target)

    def admitted(key):
        record = {
            "original_attempt": captured.attempt,
            "request_id": key.request_id,
            "mode": mode,
            "seed": seed,
            "diff": list(diff),
            "verification": None,
            "created": time.time(),
        }
        with store._transaction() as db:
            db.execute("INSERT INTO snapshots VALUES (?,?,?)", (key.attempt_id, KIND, _blob(record)))
            db.executemany(
                'INSERT OR IGNORE INTO "references" VALUES (?,?,?,?)',
                [(KIND, key.attempt_id, "attempt", key.attempt_id), (KIND, key.attempt_id, "result", captured.attempt)],
            )

    key = coordinator._schedule_pinned(
        target,
        slots,
        captured.revision,
        {KIND: captured.attempt},
        snapshot,
        documents,
        timeout,
        busy=_BUSY,
        admitted=admitted,
        keep_interactive=True,
    )
    job = RecordingJob(key, captured.attempt, mode, None, diff)
    _announce(coordinator, job, "running", None)
    return job


def finish_recording(coordinator, job: RecordingJob, *, timeout=None) -> RecordingJob:
    """Wait for a started recording; verify it only in ``replay`` mode and announce the outcome."""
    try:
        result = coordinator.wait(job.key, timeout=None if timeout is None else timeout + 5)
    except BaseException as error:
        if isinstance(error, ExecutionFailure):
            status = error.status
        else:
            coordinator.cancel(job.key)
            status = "cancelled"
        _announce(coordinator, job, status, None)
        raise
    verification = None
    if job.mode == "replay":
        from simulatte.trace import Trace

        original = coordinator.store.result(job.original_attempt)
        verification = verify_replay(original, result, Trace.open(result.path / "run.simtrace").verify())
    with coordinator.store._transaction() as db:
        row = db.execute("SELECT manifest FROM snapshots WHERE id=? AND kind=?", (job.key.attempt_id, KIND)).fetchone()
        verified = None if verification is None else {"verified": verification.verified, "reason": verification.reason}
        db.execute(
            "UPDATE snapshots SET manifest=? WHERE id=? AND kind=?",
            (_blob({**_read(row[0]), "verification": verified}), job.key.attempt_id, KIND),
        )
        db.execute(
            'INSERT OR IGNORE INTO "references" VALUES (?,?,?,?)',
            (KIND, job.key.attempt_id, "result", job.key.attempt_id),
        )
    _announce(coordinator, job, "succeeded", None if verification is None else verification.verified)
    return replace(job, verification=verification)


def record_presentation(
    coordinator,
    original_attempt: str,
    *,
    seed: int,
    overrides: Mapping[str, Any] | None = None,
    worker: str | None = None,
    timeout=None,
) -> RecordingJob:
    """Record ``original_attempt``'s captured request at ``seed`` (full level) and wait for it."""
    job = start_recording(coordinator, original_attempt, seed=seed, overrides=overrides, worker=worker, timeout=timeout)
    return finish_recording(coordinator, job, timeout=timeout)


def recordings(db, *, original_attempt: str | None = None) -> list[dict[str, Any]]:
    """Every recording (of ``original_attempt`` when given), newest first, with its attempt status."""
    if original_attempt is not None and (type(original_attempt) is not str or not _ATTEMPT.fullmatch(original_attempt)):
        raise ValueError("Invalid attempt id")
    items: list[tuple[float, str, dict[str, Any]]] = []
    for row in db.execute(
        "SELECT s.id,s.manifest,a.status FROM snapshots s LEFT JOIN attempts a ON a.id=s.id WHERE s.kind=?", (KIND,)
    ):
        record = _read(row["manifest"])
        if original_attempt is not None and record["original_attempt"] != original_attempt:
            continue
        verification = record["verification"]
        items.append(
            (
                -float(record["created"]),
                row["id"],
                {
                    "attempt_id": row["id"],
                    "request_id": record["request_id"],
                    "original_attempt": record["original_attempt"],
                    "mode": record["mode"],
                    "seed": record["seed"],
                    "status": row["status"],
                    "verified": None if verification is None else verification["verified"],
                    "created": record["created"],
                },
            )
        )
    return [item for _, _, item in sorted(items, key=lambda entry: entry[:2])]
