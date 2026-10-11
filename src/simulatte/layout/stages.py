"""Stable stage scheduling and callback-scoped tracked access."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .dependencies import MISSING
from .errors import LayoutError
from .refs import GraphRef, NodeRef, PortRef, ValueRef


@dataclass(frozen=True)
class StageSpec:
    name: str
    callback: Callable[[Any], None]
    reads: tuple[ValueRef | GraphRef, ...] = ()
    after: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reads", tuple(self.reads))
        object.__setattr__(self, "after", tuple(self.after))
        if any(not isinstance(ref, (ValueRef, GraphRef)) for ref in self.reads):
            raise LayoutError(
                "stage reads must contain ValueRef or GraphRef", code="lifecycle-error", path=("stages", "reads")
            )
        if not self.name or self.name == "declare":
            raise LayoutError(
                "stage name must be nonempty and cannot be declare", code="lifecycle-error", path=("stages",)
            )

    def to_dict(self) -> dict[str, Any]:
        """Persist grants and topology without callbacks or process-local owner ids."""
        return {"name": self.name, "reads": [ref.to_dict() for ref in self.reads], "after": list(self.after)}


def ordered_stages(stages: Sequence[StageSpec]) -> tuple[StageSpec, ...]:
    names = [stage.name for stage in stages]
    if len(set(names)) != len(names):
        raise LayoutError("duplicate stage id", code="lifecycle-error", path=("stages",))
    for stage in stages:
        unknown = set(stage.after) - {*names, "declare"}
        if unknown:
            raise LayoutError(
                f"unknown stage predecessor: {sorted(unknown)}",
                code="lifecycle-error",
                path=("stages", stage.name, "after"),
                stage=stage.name,
            )
    done = {"declare"}
    pending = list(stages)
    result = []
    while pending:
        ready = next((s for s in pending if set(s.after) <= done), None)
        if ready is None:
            # Follow unresolved predecessor edges to report an actual cycle chain.
            chain = []
            name = pending[0].name
            by_name = {s.name: s for s in pending}
            while name not in chain:
                chain.append(name)
                name = next(p for p in by_name[name].after if p not in done)
            raise LayoutError(
                "stage cycle: " + " -> ".join(chain[chain.index(name) :] + [name]),
                code="lifecycle-error",
                path=("stages", name, "after"),
                stage=name,
            )
        result.append(ready)
        pending.remove(ready)
        done.add(ready.name)
    return tuple(result)


class StageContext:
    """Valid only while its registered callback is running."""

    def __init__(self, coordinator: Any, spec: StageSpec) -> None:
        self._coordinator = coordinator
        self.spec = spec
        self.env = coordinator.env
        self.layout = coordinator.host

    def _check(self) -> None:
        if self._coordinator.active_context is not self:
            raise LayoutError("stage/bind context is no longer active", code="lifecycle-error", stage=self.spec.name)

    def physical(self, ref: ValueRef, *, default: Any = MISSING) -> Any:
        self._check()
        return self._coordinator.read_physical(self, ref, default=default)

    def graph(self, ref: GraphRef) -> Any:
        self._check()
        return self._coordinator.read_graph(self, ref)

    def select(self, ref: ValueRef, predicate: str) -> tuple[str, ...]:
        self._check()
        self._coordinator.check_grant(self, ref)
        return self._coordinator.closure.select(self._coordinator.values, ref.path, predicate)


class BindContext(StageContext):
    def node(self, ref: NodeRef | PortRef) -> Any:
        self._check()
        return self._coordinator.read_node(self, ref)
