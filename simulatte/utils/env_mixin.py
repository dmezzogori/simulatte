from __future__ import annotations

import simulatte.environment


class EnvMixin:
    """
    Adds a simpy.Environment instance to the class.
    """

    def __init__(self) -> None:
        self.env = simulatte.environment.Environment()
