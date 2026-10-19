"""Per-model input ownership backed by one project coordinator and store."""

from simulatte.execution.project_store import ModelStore
from simulatte.execution.snapshot import discover_project


class ModelContext:
    def __init__(self, coordinator, registration):
        from simulatte.execution.inputs import InputOwner

        self.coordinator = coordinator
        self.registration = registration
        self.reference = registration.entrypoint
        self.project = discover_project(
            coordinator.project.root / self.reference.split(":")[0],
            coordinator.project.root,
            layout=registration.layout,
        )
        self.store = ModelStore(coordinator.store, registration.id)
        self.inputs = InputOwner(self)
        self.prepared = None
        self.auto_session = None

    def __getattr__(self, name):
        return getattr(self.coordinator, name)

    @property
    def revision(self):
        return self.store.current_revision()

    def prepare(self, **kwargs):
        with self.coordinator._inspection_lock:
            prepared = self.coordinator.prepare(_context=self, **kwargs)
            self.prepared = prepared
            self.project = prepared.snapshot.source.project
            return prepared

    def submit(self, request, revision, **kwargs):
        # Input edits call this hook; manual Run uses the explicit admission API.
        if self.auto_session is None:
            return None
        return self.coordinator.admit_run(self.registration.id, request, revision, auto_session=self.auto_session)

    @property
    def _inputs(self):
        return self.inputs

    def start_experiment(self, experiment, **kwargs):
        return self.coordinator.start_experiment(experiment, model_id=self.registration.id, **kwargs)

    def _emit(self, kind, key, body):
        self.coordinator._emit(kind, key, {**body, "model_id": self.registration.id})
