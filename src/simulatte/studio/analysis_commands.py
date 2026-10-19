"""Studio ``analysis.*`` commands over the project's analysis store and its one local runner.

The server never imports analysis code: registrations are inspected and executions run in contained
children (``prepare_analysis`` and ``AnalysisRunner``). Values that come from analytical documents
(``details``, ``output``, ``parameters`` and the parameter schema) are tagged JSON
(``simulatte.analysis.codec.to_json``) so int64 and non-finite numbers survive the browser.
"""

from __future__ import annotations

from collections import OrderedDict
import threading

from simulatte.analysis import codec
from simulatte.analysis.selection import Selection
from simulatte.analysis.store import AnalysisStore, InsufficientInput, analysis_name

TERMINAL = frozenset(("succeeded", "failed", "cancelled", "lost"))


def _selection(raw):
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("run_ids"), list)
        or not isinstance(raw.get("experiment_run_ids"), list)
        or raw.get("configurations") is not None
        and not isinstance(raw["configurations"], list)
    ):
        raise ValueError("Selection run_ids, experiment_run_ids and configurations must be lists")
    return Selection.from_dict(raw)


def _parameters(body):
    parameters = body.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError("Analysis parameters must be an object")
    return parameters


class AnalysisCommands:
    """Dispatch ``analysis.<name>`` to ``_analysis_<name>``.

    ``analysis_state`` is polled, the least invasive option: the server's pump calls ``changes()``,
    which reads the executions admitted, rerun or cancelled here and those the runner owns, and
    reports each state that differs from the last one announced. Intermediate states of a fast
    execution may coalesce between polls; the terminal state is always announced, then forgotten.
    """

    def __init__(self, service):
        self.service = service
        self._watched = {}
        self._finished = OrderedDict()
        self._lock = threading.Lock()

    def __call__(self, kind, body):
        handler = getattr(self, "_" + kind.replace(".", "_"), None) if kind.startswith("analysis.") else None
        if handler is None:
            raise ValueError("Unsupported analysis command")
        try:
            return handler(body)
        except InsufficientInput as error:
            return {
                "ok": False,
                "error": {
                    "code": "insufficient_input",
                    "message": str(error)[:4096],
                    "details": codec.to_json(error.details),
                },
            }

    def _store(self):
        store = self.service.store or self.service.reader
        if store is None:
            raise ValueError("Saved analyses are unavailable")
        return AnalysisStore(store)

    def _writable(self):
        self.service._writable()
        return AnalysisStore(self.service.store)

    def _registration(self, registration_id):
        workspace = self.service.workspace
        analyses = () if workspace is None else workspace.analyses
        return next((a for a in analyses if a.id == registration_id), None)

    def _definition(self, definition):
        """``{id, name, entrypoint, registration_key?, parameters, requirements}`` with a tagged schema."""
        registration_id = definition.pop("registration_id")
        registration = self._registration(registration_id)
        definition["name"] = registration_id if registration is None else registration.name
        if registration is not None:
            definition["registration_key"] = registration.key
        definition["parameters"] = codec.to_json(definition["parameters"])
        return definition

    def _get(self, analysis, identity):
        reply = analysis.get(identity)
        definition, execution = self._definition(reply["definition"]), reply["execution"]
        execution["name"] = execution["name"] or definition["name"]
        execution["parameters"] = codec.to_json(execution["parameters"])
        reply["details"] = codec.to_json(reply["details"])
        reply["output"] = None if reply["output"] is None else codec.to_json(reply["output"])
        return reply

    def _analysis_list(self, body):
        page = self._store().list(body.get("cursor"), body.get("limit", 50))
        for item in page["items"]:
            registration_id = item.pop("registration_id")
            registration = self._registration(registration_id)
            if registration is not None:
                item["registration_key"] = registration.key
            item["name"] = item["name"] or (registration_id if registration is None else registration.name)
        return {"ok": True, **page}

    def _analysis_get(self, body):
        return {"ok": True, **self._get(self._store(), body["execution_id"])}

    def _preview(self, analysis, preview, definition, generation, parameters):
        analysis.check(preview.id, definition_id=definition, parameters=parameters, generation_id=generation)
        stored = analysis.definition(definition)
        return {
            "ok": True,
            "preview_id": preview.id,
            "snapshot_hash": preview.snapshot_hash,
            "available": preview.available,
            "planned": preview.planned,
            "missing": preview.missing,
            "partial": preview.partial,
            "definition_id": definition,
            "generation_id": generation,
            # The parameter form before admission; the same shape as ``analysis.get``.
            "definition": self._definition(
                {
                    "id": definition,
                    "registration_id": stored["id"],
                    "entrypoint": stored["entrypoint"],
                    "parameters": stored["parameters"],
                    "requirements": stored["requirements"],
                }
            ),
        }

    def _analysis_preview(self, body):
        analysis = self._writable()
        selection, parameters = _selection(body["selection"]), _parameters(body)
        from simulatte.analysis.command import prepare_analysis

        service = self.service
        definition, generation = prepare_analysis(
            service.store,
            body["registration_key"],
            environments=service.environments,
            timeout=30 if service.timeout is None else service.timeout,
        )
        return self._preview(analysis, analysis.preview(selection), definition, generation, parameters)

    def _submit(self, analysis, runner, item, name):
        if name:
            analysis.rename(item.id, name)
        self.watch(item.id, None)
        runner.submit(item.id)
        return {"ok": True, "execution": self._get(analysis, item.id)["execution"]}

    def _analysis_admit(self, body):
        analysis = self._writable()
        name = None if body.get("name") is None else analysis_name(body["name"])
        runner = self.service.analyses()
        item = analysis.admit(
            body["preview_id"],
            definition_id=body["definition_id"],
            parameters=_parameters(body),
            generation_id=body["generation_id"],
        )
        return self._submit(analysis, runner, item, name)

    def _analysis_rerun(self, body):
        analysis = self._writable()
        name = analysis.get(body["execution_id"])["details"].get("name")
        runner = self.service.analyses()
        return self._submit(analysis, runner, analysis.rerun(body["execution_id"]), name)

    def _analysis_refresh(self, body):
        analysis = self._writable()
        execution = analysis.get(body["execution_id"])["execution"]
        preview = analysis.refresh(execution["id"])
        return self._preview(
            analysis, preview, execution["definition_id"], execution["generation_id"], execution["parameters"]
        )

    def _analysis_cancel(self, body):
        analysis = self._writable()
        identity = body["execution_id"]
        before = analysis.execution(identity).state
        self.service.analyses().cancel(identity)
        self.watch(identity, before)
        return {"ok": True, "execution": self._get(analysis, identity)["execution"]}

    def _analysis_annotate(self, body):
        analysis = self._writable()
        return {"ok": True, "revision": analysis.annotate(body["execution_id"], body["text"])}

    def watch(self, identity, state):
        with self._lock:
            self._finished.pop(identity, None)
            self._watched.setdefault(identity, state)

    def _owned(self):
        runner = self.service.analysis_runner
        if runner is None:
            return ()
        with runner._lock:
            return tuple(runner._futures)

    def watching(self):
        with self._lock:
            if self._watched:
                return True
        return bool(self._owned())

    def changes(self):
        """State changes since the last call, as ``analysis_state`` bodies."""
        for identity in self._owned():
            with self._lock:
                if identity not in self._finished:
                    self._watched.setdefault(identity, None)
        with self._lock:
            watched = dict(self._watched)
        if not watched:
            return []
        analysis = AnalysisStore(self.service.store)
        changes = []
        for identity, last in watched.items():
            try:
                state = analysis.execution(identity).state
            except KeyError:
                state = None
            with self._lock:
                if state is None or state in TERMINAL:
                    self._watched.pop(identity, None)
                    # A finished future may linger in the runner briefly; do not announce it twice.
                    self._finished[identity] = None
                    while len(self._finished) > 256:
                        self._finished.popitem(last=False)
                else:
                    self._watched[identity] = state
            if state is not None and state != last:
                changes.append({"execution_id": identity, "state": state})
        return changes
