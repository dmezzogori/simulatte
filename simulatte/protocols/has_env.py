from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from simulatte.environment import Environment


class HasEnv(Protocol):
    env: Environment
