"""Layout diagnostics and lifecycle errors."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LayoutDiagnostic:
    code: str
    severity: Literal["error", "warning"]
    path: tuple[str, ...]
    stage: str | None
    target: str | None
    message: str
    related_paths: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", tuple(self.path))
        object.__setattr__(self, "related_paths", tuple(tuple(path) for path in self.related_paths))
        if self.severity not in ("error", "warning"):
            raise ValueError("diagnostic severity must be error or warning")


class LayoutError(ValueError):
    """Invalid layout declaration, patch or resolved model."""

    def __init__(self, message: str, *, diagnostics: tuple[LayoutDiagnostic, ...] = ()) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)


class LayoutNotReady(LayoutError):
    """A handle or layout-dependent value was read before binding."""


def sort_diagnostics(
    diagnostics: Sequence[LayoutDiagnostic], stage_order: Sequence[str]
) -> tuple[LayoutDiagnostic, ...]:
    """Use first declaration in execution order; file/unstaged diagnostics follow."""
    order = {stage: index for index, stage in enumerate(dict.fromkeys(stage_order))}
    return tuple(
        sorted(
            set(diagnostics),
            key=lambda item: (order.get(item.stage, len(order)), item.path, item.code, item.target or ""),
        )
    )
