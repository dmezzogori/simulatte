from __future__ import annotations

from simpy.resources.base import BoundClass
from simpy.resources.resource import PriorityRequest, PriorityResource, Release

from simulatte.utils.priority import Priority


class MonitoredRequest(PriorityRequest):
    """
    Request for the MonitoredResource.
    """

    resource: MonitoredResource

    def __init__(self, resource: MonitoredResource, *, priority=Priority.NORMAL, preempt=False) -> None:
        super().__init__(resource, priority=priority, preempt=preempt)

    def __enter__(self):
        self.start_time = self.env.now
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        end_time = self.env.now
        self.resource.worked_time += end_time - self.start_time
        return super().__exit__(exc_type, exc_val, exc_tb)


class MonitoredResource(PriorityResource):
    """
    PriorityResource with monitoring capabilities.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._worked_time = 0
        self._saturation_history = []

    @property
    def worked_time(self) -> int:
        return self._worked_time

    @worked_time.setter
    def worked_time(self, value: int) -> None:
        self._worked_time = value
        self._saturation_history.append((self._env.now, self.saturation))

    @property
    def idle_time(self) -> int:
        return self._env.now - self._worked_time

    @property
    def saturation(self) -> float:
        return self._worked_time / self._env.now

    request = BoundClass(MonitoredRequest)  # type: ignore
    release = BoundClass(Release)  # type: ignore
