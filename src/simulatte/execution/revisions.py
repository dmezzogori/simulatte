"""Data associations between input revisions and immutable execution trajectories."""

from __future__ import annotations

from dataclasses import dataclass

from simulatte.execution.schema import AttemptKey, Revision
from simulatte.layout.bundle import LayoutBundle


@dataclass(frozen=True, slots=True)
class PresentationView:
    revision: Revision
    key: AttemptKey
    bundle: LayoutBundle
    committed: bool
