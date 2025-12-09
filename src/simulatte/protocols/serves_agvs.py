from __future__ import annotations

from typing import Any, Protocol

from simulatte.typings import History


class ServesAGV(Protocol):
    input_agvs_queue: int
    input_agvs_queue_history: History[int]

    output_agvs_queue: int
    output_agvs_queue_history: History[int]

    def load_agv(self, *, feeding_operation: Any) -> Any: ...

    def unload_agv(self, **kwargs) -> Any: ...
