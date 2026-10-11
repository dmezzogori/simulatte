"""Layout diagnostics and lifecycle errors."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
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
    """Invalid layout declaration, patch or resolved model, with structured diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: tuple[LayoutDiagnostic, ...] = (),
        code: str = "invalid-layout",
        path: tuple[str, ...] = (),
        stage: str | None = None,
        target: str | None = None,
        related_paths: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics) or (
            LayoutDiagnostic(
                code,
                "error",
                path,
                stage,
                target if target is not None else (path[1] if len(path) > 1 and path[0] == "placements" else None),
                message,
                related_paths,
            ),
        )


class LayoutNotReady(LayoutError):
    """A handle or layout-dependent value was read before binding."""


@contextmanager
def diagnostic_context(*, path: tuple[str, ...] = (), stage: str | None = None, target: str | None = None):
    """Add known caller context without replacing a more precise originating location."""
    try:
        yield
    except LayoutError as error:
        error.diagnostics = tuple(
            replace(item, path=item.path or path, stage=item.stage or stage, target=item.target or target)
            for item in error.diagnostics
        )
        raise


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
