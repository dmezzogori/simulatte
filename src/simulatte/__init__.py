"""Simulatte: discrete-event simulation for production planning and control and intralogistics.

The package exports the stable entry points. Component classes (``Server``, ``ShopFloor``, ``FleetCoordinator`` and
so on) stay importable from their modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simulatte.runner import Runner

from simulatte.environment import Environment
from simulatte.events import DomainEvent, Event, ObserverEvent
from simulatte.kpi import KPI, Collector
from simulatte.provenance import Provenance
from simulatte.trace import Trace, TraceRecorder

__all__ = [
    "KPI",
    "Collector",
    "DomainEvent",
    "Environment",
    "Event",
    "ObserverEvent",
    "Provenance",
    "Runner",
    "Trace",
    "TraceRecorder",
]


def __getattr__(name: str) -> Any:
    if name == "Runner":
        from simulatte.runner import Runner

        globals()[name] = Runner
        return Runner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
