from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from simulatte.stores.operation import InputOperation, Operation, OutputOperation
from simulatte.unitload.case_container import CaseContainer
from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


def test_operation_position_and_equality(env):
    location = cast(WarehouseLocation, SimpleNamespace(x=3, y=2))
    unit_load = cast(CaseContainer, SimpleNamespace())
    op_a = Operation(unit_load=unit_load, location=location, priority=5, env=env)
    op_b = Operation(unit_load=unit_load, location=location, priority=1, env=env)

    assert op_a.position == 3
    assert op_a.floor == 2
    assert op_a == op_b


def test_input_operation_sets_lift_state(env):
    location = cast(WarehouseLocation, SimpleNamespace(x=0, y=0))
    unit_load = cast(CaseContainer, SimpleNamespace())
    op = InputOperation(unit_load=unit_load, location=location, priority=0, env=env)

    assert op.lift_process is None
    assert not op.lifted.triggered


def test_output_operation_is_subclass(env):
    location = cast(WarehouseLocation, SimpleNamespace(x=1, y=1))
    unit_load = cast(CaseContainer, SimpleNamespace())
    op = OutputOperation(unit_load=unit_load, location=location, priority=0, env=env)

    assert isinstance(op, Operation)
    assert op.priority == 0
