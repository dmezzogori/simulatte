"""Opaque backend-state projection; no claim of per-actor event replay."""

from __future__ import annotations

from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import DomainEvent, event_type


class BackendState(Entity, kind="execution_backend"):
    state_schema = StateSchema(
        {
            "backend": FieldSpec("str"),
            "fingerprint_schema": FieldSpec("str"),
            "state_digest": FieldSpec("str"),
        }
    )

    def __init__(self, env, scope, capabilities):
        self.backend = capabilities.name
        self.fingerprint_schema = capabilities.state_schema
        self.state_digest = ""
        env.entities.attach(self, name=scope, label="Execution backend state")


@event_type("execution.backend_state_captured", touches={"scope": ("state_digest",)})
class BackendStateCaptured(DomainEvent):
    scope: str
    state_digest: str
