from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simulatte.experimental.gymnasium import SimulatteEnv

__all__ = ["SimulatteEnv"]


def __getattr__(name: str) -> Any:
    if name == "SimulatteEnv":
        from simulatte.experimental.gymnasium import SimulatteEnv

        globals()[name] = SimulatteEnv
        return SimulatteEnv
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
