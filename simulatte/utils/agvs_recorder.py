from __future__ import annotations

from functools import wraps
from typing import TypeVar

_T = TypeVar("_T")


def agvs_recorder(cls: type[_T]) -> type[_T]:
    """
    Class decorator to keep history of the AGVs input and output queues
    of any class that implements ServesAGVs protocol.

    Args:
        cls (type): The class to be decorated.

    Returns:
        type: The decorated class with AGVs queue recording capability.
    """

    original_init = cls.__init__

    @wraps(original_init)
    def new_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        self.input_agvs_queue = 0
        self.input_agvs_queue_history = []
        self.output_agvs_queue = 0
        self.output_agvs_queue_history = []

    original_load_agv = cls.load_agv

    @wraps(original_load_agv)
    def new_load_agv(self, **kwargs):
        self.output_agvs_queue += 1
        self.output_agvs_queue_history.append((self.env.now, self.retrieval_jobs_counter))

        ret = original_load_agv(self, **kwargs)

        self.output_agvs_queue -= 1
        self.output_agvs_queue_history.append((self.env.now, self.retrieval_jobs_counter))

        return ret

    original_unload_agv = cls.unload_agv

    @wraps(original_unload_agv)
    def new_unload_agv(self, *args, **kwargs):
        self.input_agvs_queue += 1
        self.input_agvs_queue_history.append((self.env.now, self.storage_jobs_counter))

        ret = original_unload_agv(self, *args, **kwargs)

        self.input_agvs_queue -= 1
        self.input_agvs_queue_history.append((self.env.now, self.storage_jobs_counter))

        return ret

    setattr(cls, "__init__", new_init)
    setattr(cls, "load_agv", new_load_agv)
    setattr(cls, "unload", new_unload_agv)
    return cls
