from __future__ import annotations

from simulatte.environment import Environment
from simulatte.simpy_extension.multi_store import MultiStore


class SequentialMultiStore(MultiStore):
    """
    Alias to MultiStore kept for backward compatibility.
    """

    def __init__(self, *, env: Environment, capacity: float = float("inf")) -> None:
        super().__init__(env=env, capacity=capacity)
