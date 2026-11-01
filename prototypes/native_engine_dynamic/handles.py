"""Opaque, stable stream-local identities; no native pointers escape."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobHandle:
    _owner: object
    id: int
