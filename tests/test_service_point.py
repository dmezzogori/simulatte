from __future__ import annotations

from simulatte.location import Location
from simulatte.service_point.service_point import ServicePoint


def test_service_point_stores_location_and_env():
    service_point = ServicePoint(location=Location(), capacity=1)

    assert service_point.location.name == "Location"
    assert service_point.capacity == 1
    assert service_point.env.now == 0


def test_service_point_respects_priority_ordering():
    service_point = ServicePoint(location=Location(), capacity=1)
    env = service_point.env
    served: list[str] = []

    def blocker():
        with service_point.request() as req:
            yield req
            yield env.timeout(0.1)

    def customer(name: str, priority: int):
        with service_point.request(priority=priority) as req:
            yield req
            served.append(name)
            yield env.timeout(1)

    env.process(blocker())
    env.process(customer("slow", priority=1))
    env.process(customer("fast", priority=0))

    env.run()

    assert served == ["fast", "slow"]
