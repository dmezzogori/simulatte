from __future__ import annotations

from simulatte.environment import Environment
from simulatte.resources.monitored_resource import MonitoredResource
from simulatte.utils.priority import Priority


def test_monitored_resource_tracks_work_and_saturation():
    env = Environment()
    resource = MonitoredResource(env=env, capacity=1)

    def worker():
        with resource.request(priority=Priority.HIGH) as req:
            yield req
            yield env.timeout(5)

    env.process(worker())
    env.run()

    assert resource.worked_time == 5
    assert resource.idle_time == 0
    assert resource.saturation == 1
    assert resource._saturation_history[-1][1] == resource.saturation
