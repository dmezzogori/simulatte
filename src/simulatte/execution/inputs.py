"""Serialized, data-only input ownership shared by Studio and the coordinator."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any
from collections.abc import Mapping

from simulatte.execution.coordinator import ExecutionFailure, PreparedModel, selected_model
from simulatte.execution.layout_files import read_layout_file, backup_layout, stage_layout
from simulatte.execution.schema import AttemptKey, ExecutionRequest, Revision, _mapping, _plain
from simulatte.execution.store import RevisionConflict
from simulatte.execution.snapshot import discover_project
from simulatte.layout.bundle import LayoutBundle, reresolve_presentation
from simulatte.layout.layers import FileLayer
from simulatte.layout.identity import layout_hash
from simulatte.model import ParameterSchema, validate_parameters


@dataclass(frozen=True, slots=True)
class InputState:
    revision: Revision
    options: Mapping[str, Any]
    issues: Mapping[str, Any]
    request: ExecutionRequest | None

    def __post_init__(self):
        for name in ("options", "issues"):
            object.__setattr__(self, name, _mapping(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class LayoutValidation:
    revision: Revision
    requires_rerun: bool
    key: AttemptKey | None = None
    bundle: LayoutBundle | None = None
    changed_paths: tuple[tuple[str, ...], ...] = ()


class InputOwner:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._prepared: PreparedModel | None = None
        self._state: InputState | None = None
        self._captured = None
        self._refreshing = None

    @property
    def state(self):
        with self.coordinator._condition:
            return self._state

    def captured_inputs(self):
        """Return one consistent immutable observation for the project watcher."""
        with self.coordinator._condition:
            if self._state is None or self._prepared is None or self._captured is None:
                raise RuntimeError("Model metadata must be prepared before watching inputs")
            return self._state, self._prepared, self._captured

    def _check(self, base):
        if self.coordinator._closed or self.coordinator._fatal is not None:
            raise RuntimeError("Coordinator cannot accept input changes")
        if self.coordinator.store.current_revision() != base:
            raise RevisionConflict("Project revision changed; reload before editing")

    def bind(self, prepared: PreparedModel, *, initial=None, initial_error: str | None = None):
        """Adopt inspected metadata, retaining only still-valid parameter values."""
        with self.coordinator._condition:
            self._check(prepared.revision)
            if (initial is not None or initial_error is not None) and self._state is not None:
                raise ValueError("Options are initial only; use Apply after binding")
            captured = read_layout_file(self.coordinator.project.root, self.coordinator.project.layout)
            if captured.layer().to_dict() != _plain(prepared.layout):
                raise RevisionConflict("Layout changed on disk during preparation")
            selected = selected_model(prepared.metadata)
            schema = ParameterSchema.from_dict(selected["parameters"])
            initial_options: dict[str, Any] | None = None
            if initial is not None:
                # Validate a partial startup draft without admitting it as a run.
                if not isinstance(initial, Mapping) or set(initial) - {
                    "parameters",
                    "seed",
                    "stopping",
                    "level",
                    "kpis",
                }:
                    raise ValueError("Unknown initial execution input")
                supplied = initial.get("parameters", {})
                partial = ParameterSchema(tuple(field for field in schema.fields if field.name in supplied))
                parameters = validate_parameters(partial, supplied)
                draft = ExecutionRequest(
                    model=prepared.model,
                    source_hash=prepared.snapshot.source.content_hash,
                    input_hash=prepared.snapshot.inputs.content_hash,
                    layout_hash=layout_hash("file", prepared.layout),
                    layout_revision=prepared.revision.number,
                    parameters=parameters,
                    seed=initial.get("seed", 0),
                    stopping=initial.get("stopping", selected["stopping"]),
                    level=initial.get("level", "full"),
                    kpis=initial.get("kpis", ()),
                    runtime=prepared.runtime,
                )
                initial_options = {
                    "parameters": _plain(draft.parameters),
                    "seed": str(draft.seed),
                    "stopping": _plain(draft.stopping),
                    "level": draft.level,
                    "kpis": list(draft.kpis),
                }
            old = (
                (initial_options or {}).get("parameters", {})
                if self._state is None
                else _plain(self._state.options["parameters"])
            )
            parameters = {}
            issues = (
                ({} if initial_error is None else {"startup": initial_error})
                if self._state is None
                else {
                    key: value for key, value in _plain(self._state.issues).items() if key not in ("source", "layout")
                }
            )
            names = {field.name for field in schema.fields}
            for name in old.keys() - names:
                issues[name] = "Parameter was removed from the model schema"
            for field in schema.fields:
                if field.name in old:
                    try:
                        parameters[field.name] = field.validate(old[field.name])
                        continue
                    except ValueError as error:
                        issues[field.name] = str(error)
                if field.required:
                    issues.setdefault(field.name, "Required parameter missing")
                else:
                    parameters[field.name] = field.default
            options = {
                "parameters": parameters,
                "seed": "0",
                "stopping": _plain(selected["stopping"]),
                "level": "full",
                "kpis": [],
            }
            if initial_options is not None:
                options.update({key: value for key, value in initial_options.items() if key != "parameters"})
            if self._state is not None:
                for name in ("seed", "level", "kpis"):
                    options[name] = _plain(self._state.options[name])
                previous_stop = _plain(self._state.options["stopping"])
                if previous_stop["type"] == options["stopping"]["type"]:
                    options["stopping"] = previous_stop
                else:
                    issues["stopping"] = "Stopping policy family changed; review the new model default"
            request = None
            if not issues:
                request = self._request(prepared, options)
            state = self._commit(prepared, options, issues, request, captured=captured)
            self.coordinator.project = prepared.snapshot.source.project
            return state

    def _commit(self, prepared, options, issues, request, *, captured=None, view=None):
        captured = self._captured if captured is None else captured
        inputs = {
            "source": prepared.snapshot.source.content_hash,
            "inputs": prepared.snapshot.inputs.content_hash,
            "model": prepared.model,
            "metadata": _plain(prepared.metadata),
            "layout": _plain(prepared.layout),
            "runtime": _plain(prepared.runtime),
            "options": options,
            "issues": issues,
            "layout_file_sha256": None if captured is None else captured.digest,
        }
        if request is not None:
            request = replace(request, layout_revision=prepared.revision.number + 1)
        manifest = None if request is None else self.coordinator._preparation_manifest(prepared, request)
        revision = self.coordinator.store.create_revision(
            inputs, expected=prepared.revision, revoke_active=view is None, view=view, preparation=manifest
        )
        self._prepared = replace(prepared, revision=revision)
        self._captured = captured
        self._state = InputState(revision, options, issues, request)
        self.coordinator._condition.notify_all()
        self.coordinator._emit(
            "revision_changed", None, {"revision": {"number": revision.number, "digest": revision.digest}}
        )
        return self._state

    def _request(self, prepared, options):
        seed = options["seed"]
        if type(seed) is str:
            if re.fullmatch("0|[1-9][0-9]{0,18}", seed) is None:
                raise ValueError("Seed must be canonical decimal text")
            seed = int(seed)
        return self.coordinator._build_request(prepared, **{**options, "seed": seed})

    def apply(self, base: Revision, changes: Mapping[str, Any], *, auto_run=True, timeout=None):
        with self.coordinator._condition:
            self._check(base)
            state, prepared = self._state, self._prepared
            if state is None or prepared is None or state.revision != base:
                raise RevisionConflict("Input metadata must be refreshed before editing")
            if "layout" in state.issues:
                raise ValueError("Layout must be refreshed before applying execution inputs")
            if "source" in state.issues:
                raise ValueError("Source must be refreshed before applying execution inputs")
            if not isinstance(changes, Mapping) or set(changes) - set(state.options):
                raise ValueError("Unknown execution input")
            options = {**_plain(state.options), **_plain(dict(changes))}
            schema = ParameterSchema.from_dict(selected_model(prepared.metadata)["parameters"])
            options["parameters"] = validate_parameters(schema, options["parameters"])
            request = self._request(prepared, options)
            options = {
                "parameters": _plain(request.parameters),
                "seed": str(request.seed),
                "stopping": _plain(request.stopping),
                "level": request.level,
                "kpis": list(request.kpis),
            }
            if options == _plain(state.options) and not state.issues:
                return state
            state = self._commit(prepared, options, {}, request)
        if auto_run:
            self.coordinator.submit(state.request, state.revision, timeout=timeout)
        return state

    def validate_layout(self, base: Revision, patch: Mapping[str, Any]):
        """Classify from the captured dependency closure without executing Python."""
        with self.coordinator._condition:
            self._check(base)
            state = self._state
            if state is None or state.revision != base:
                raise RevisionConflict("Input metadata must be refreshed before editing")
            view = self.coordinator.store.presentation_view(revision=base)
            original = None if view is None else self.coordinator.store.request(view.key.request_id)
        layer = FileLayer(patch)
        result = LayoutValidation(base, True)
        if view is not None and original is not None and state.request is not None:

            def nonlayout(request):
                data = request.to_dict()
                del data["layout_hash"], data["layout_revision"]
                return data

            if nonlayout(original) == nonlayout(state.request) and view.bundle.reusable:
                resolved = reresolve_presentation(view.bundle, layer.data)
                result = LayoutValidation(
                    base, resolved.requires_rerun, view.key, resolved.bundle, resolved.changed_paths
                )
        with self.coordinator._condition:
            self._check(base)
            # A cancellation/lost attempt invalidates its provisional proof even
            # when there was no input revision change during pure resolution.
            current = self.coordinator.store.presentation_view(revision=base)
            if result.key is not None and (current is None or current.key != result.key):
                return LayoutValidation(base, True)
            return result

    def preview_layout(self, base: Revision, patch: Mapping[str, Any], *, timeout=30):
        """Pure presentation resolution, or a disposable physical preparation child."""
        validation = self.validate_layout(base, patch)
        if not validation.requires_rerun:
            return validation
        with self.coordinator._condition:
            self._check(base)
            state, prepared = self._state, self._prepared
            if state is None or prepared is None or state.issues:
                raise ValueError("Resolve execution input issues before previewing physical changes")
            layer = FileLayer(patch).to_dict()
            prepared = replace(prepared, layout=layer)
            request = self._request(prepared, _plain(state.options))
        bundle = self.coordinator.preview(prepared, request, layer, timeout=timeout)
        with self.coordinator._condition:
            self._check(base)
            return replace(validation, bundle=bundle)

    def _adopt_layout(self, captured, validation):
        prepared, state = self._prepared, self._state
        assert prepared is not None and state is not None
        layer = captured.layer().to_dict()
        documents = {**_plain(prepared.documents), "layout_ref": self.coordinator._document("layout", layer)}
        prepared = replace(prepared, layout=layer, documents=documents)
        options = _plain(state.options)
        issues = {key: value for key, value in _plain(state.issues).items() if key != "layout"}
        request = None if issues else self._request(prepared, options)
        view = None if validation.requires_rerun else (validation.key, validation.bundle)
        return self._commit(prepared, options, issues, request, captured=captured, view=view)

    def observe_layout(self, base: Revision, *, auto_run=True, timeout=None):
        captured = read_layout_file(self.coordinator.project.root, self.coordinator.project.layout)
        with self.coordinator._condition:
            self._check(base)
            state, prepared = self._state, self._prepared
            if state is None or prepared is None or state.revision != base:
                raise RevisionConflict("Input metadata must be refreshed before editing")
            if captured == self._captured:
                return state
        try:
            layer = captured.layer()
        except ValueError as error:
            with self.coordinator._condition:
                self._check(base)
                return self._commit(
                    prepared,
                    _plain(state.options),
                    {**_plain(state.issues), "layout": str(error)[:4096]},
                    None,
                    captured=captured,
                )
        validation = self.validate_layout(base, layer.data)
        with self.coordinator._condition:
            self._check(base)
            state = self._adopt_layout(captured, validation)
        if auto_run and validation.requires_rerun and state.request is not None:
            self.coordinator.submit(state.request, state.revision, timeout=timeout)
        elif auto_run and "source" in state.issues:
            return self.refresh(state.revision, timeout=timeout)
        return state

    def invalidate(self, base: Revision, message: str):
        """Pause automatic execution and revoke provisional data before recapture."""
        with self.coordinator._condition:
            self._check(base)
            state, prepared = self._state, self._prepared
            if state is None or prepared is None or state.revision != base:
                raise RevisionConflict("Input metadata must be refreshed before editing")
            invalid = self._commit(
                prepared,
                _plain(state.options),
                {**_plain(state.issues), "source": message[:4096]},
                None,
            )
            self._refreshing = None
            return invalid

    def refresh(self, base: Revision, *, auto_run=True, timeout=None):
        """Fence old work immediately; only this latest inspection may adopt inputs."""
        token = object()
        with self.coordinator._condition:
            invalid = self.invalidate(base, "Refreshing source and declared inputs")
            self._refreshing = token

        def preparing(revision):
            # Called under the coordinator condition at each inspection/capture
            # revision, before admission or publication of prepared metadata.
            if self._refreshing is not token:
                raise RevisionConflict("Source refresh was superseded")
            assert self._state is not None and self._prepared is not None
            self._state = replace(self._state, revision=revision)
            self._prepared = replace(self._prepared, revision=revision)
            self.coordinator._emit(
                "revision_changed", None, {"revision": {"number": revision.number, "digest": revision.digest}}
            )

        try:
            project = self.coordinator.project
            discovered = discover_project(project.root / project.model, project.root, layout=project.layout)
            prepared = self.coordinator.prepare(
                expected=invalid.revision, timeout=timeout, _on_revision=preparing, _project=discovered
            )
            with self.coordinator._condition:
                if self._refreshing is not token:
                    raise RevisionConflict("Source refresh was superseded")
                state = self.bind(prepared)
                self._refreshing = None
        except Exception as error:
            with self.coordinator._condition:
                if self._refreshing is token:
                    self._refreshing = None
                    assert self._prepared is not None and self._state is not None
                    if self.coordinator.store.current_revision() == self._state.revision:
                        captured = self._captured
                        try:
                            project = self.coordinator.project
                            captured = read_layout_file(project.root, project.layout)
                        except (OSError, ValueError):
                            pass  # Keep the previous baseline when the file itself is unreadable.
                        self._commit(
                            self._prepared,
                            _plain(self._state.options),
                            {**_plain(self._state.issues), "source": str(error)[:4096]},
                            None,
                            captured=captured,
                        )
            raise
        if auto_run and state.request is not None:
            self.coordinator.submit(state.request, state.revision, timeout=timeout)
        return state

    def write_layout(self, base: Revision, patch: Mapping[str, Any], *, auto_run=True, timeout=None):
        validation = self.validate_layout(base, patch)
        with self.coordinator._condition:
            self._check(base)
            captured, state = self._captured, self._state
            assert captured is not None and state is not None
        root, name = self.coordinator.project.root, self.coordinator.project.layout
        if read_layout_file(root, name) != captured:
            self.observe_layout(base, auto_run=auto_run, timeout=timeout)
            raise RevisionConflict("Layout changed on disk; reload before saving")
        layer = FileLayer(patch)
        if not {"layout", "source"}.intersection(state.issues) and captured.layer().to_dict() == layer.to_dict():
            with self.coordinator._condition:
                self._check(base)
                return state
        try:
            with stage_layout(root, name, layer) as staged:
                with self.coordinator._condition:
                    self._check(base)
                    backup_layout(self.coordinator.store, captured)
                    try:
                        staged.replace(expected=captured)
                        state = self._adopt_layout(staged.replacement, validation)
                    except RevisionConflict:
                        raise
                    except Exception as error:
                        # A rename or SQL failure can leave disk and indexed inputs
                        # different. Pause execution even when no new index write works.
                        try:
                            assert self._prepared is not None
                            self._commit(
                                self._prepared,
                                _plain(state.options),
                                {**_plain(state.issues), "layout": str(error)[:4096]},
                                None,
                            )
                        except Exception:
                            self.coordinator._fatal = ExecutionFailure(
                                None, "failed", {"message": str(error)[:4096], "index_terminal_state": "not_durable"}
                            )
                            self.coordinator._pending = None
                            self.coordinator._condition.notify_all()
                        raise
        except RevisionConflict:
            if self.coordinator.store.current_revision() == base:
                self.observe_layout(base, auto_run=auto_run, timeout=timeout)
            raise
        if auto_run and validation.requires_rerun and state.request is not None:
            self.coordinator.submit(state.request, state.revision, timeout=timeout)
        elif auto_run and "source" in state.issues:
            return self.refresh(state.revision, timeout=timeout)
        return state
