from __future__ import annotations

from typing import cast

from simulatte.controllers.distance_manager import DistanceController
from simulatte.distance.distance import Distance
from simulatte.location import Location
from simulatte.controllers.system_controller import SystemController


class DummyDistance(Distance):
    @property
    def as_distance(self) -> float:  # type: ignore[override]
        return 42.0


def test_distance_controller_register_and_call():
    controller = DistanceController(DistanceClass=DummyDistance)
    system = cast(SystemController, object())
    controller.register_system(system)

    result = controller(from_=Location(), to=Location())

    assert isinstance(result, DummyDistance)
    assert result.system is system
    assert result.as_distance == 42.0
