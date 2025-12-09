from __future__ import annotations


from typing import cast

from simulatte.controllers.system_controller import SystemController
from simulatte.environment import Environment
from simulatte.picking_cell.cell import PickingCell
from simulatte.resources.monitored_resource import MonitoredResource
from simulatte.resources.store import Store
from simulatte.robot import Robot
from simulatte.simpy_extension.sequential_store.sequential_store import SequentialStore


class DummySystem:
    def __init__(self):
        self.env = Environment()


def test_picking_cell_can_instantiate_without_main_process():
    system = DummySystem()
    env = system.env
    input_queue = Store(env)
    output_queue = SequentialStore()
    building_point = MonitoredResource(env, capacity=1)
    robot = Robot(pick_timeout=1, place_timeout=1, rotation_timeout=1)

    cell = PickingCell(
        system=cast(SystemController, system),
        input_queue=input_queue,
        output_queue=output_queue,
        building_point=building_point,
        robot=robot,
        feeding_area_capacity=2,
        staging_area_capacity=2,
        internal_area_capacity=2,
        workload_unit="cases",
        register_main_process=False,
    )

    assert cell.system is system
    assert cell.feeding_area.capacity == 2
    assert cell.staging_area.capacity == 2
    assert cell.internal_area.capacity == 2
    assert cell.robot is robot
    assert cell.input_queue is input_queue
    assert cell.output_queue is output_queue
