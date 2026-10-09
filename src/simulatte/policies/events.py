"""Events emitted by release policies."""

from __future__ import annotations

from simulatte.events import DomainEvent, event_type

__all__ = ["PolicyDecision"]


@event_type("policy.decision")
class PolicyDecision(DomainEvent):
    """A release policy decided something about a job; it changes no entity state.

    `policy` is the class name of the deciding policy (policies are not entities). `action` is ``"release"``
    (the job leaves the pool for the shop floor), ``"postpone"`` (the job leaves the pool now and reaches the
    shop floor after a short delay) or ``"force_pin"`` (DRACO pins the winner at the server's queue head). The
    resulting ``psp.exited`` and ``shopfloor.entered`` events carry the state changes.
    """

    policy: str
    job: str
    action: str
