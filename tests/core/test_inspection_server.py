from __future__ import annotations

from typing import Any

import pytest

from simulatte.environment import Environment
from simulatte.inspection_server import InspectionServer
from simulatte.job import BaseJob, ProductionJob
from simulatte.shopfloor import ShopFloor


class ConcreteInspectionServer(InspectionServer):
    """Concrete implementation for testing."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rework_calls: list[BaseJob] = []

    def rework(self, job: BaseJob) -> None:
        self.rework_calls.append(job)


def test_inspection_server_normal_processing() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = ConcreteInspectionServer(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)

    sf.add(job)
    env.run()

    assert job.done
    assert server.rework_calls == []  # No rework triggered
    assert job.rework is False


def test_inspection_server_triggers_rework() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = ConcreteInspectionServer(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)

    # Set rework flag before processing
    job.rework = True

    sf.add(job)
    env.run()

    assert job.done
    assert server.rework_calls == [job]  # Rework was triggered
    assert job.rework is False  # Flag reset after processing


def test_inspection_server_rework_resets_flag() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = ConcreteInspectionServer(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)

    job.rework = True
    sf.add(job)
    env.run()

    # Flag should be False after processing
    assert job.rework is False


def test_inspection_server_base_raises_not_implemented() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = InspectionServer(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)

    with pytest.raises(NotImplementedError):
        server.rework(job)


def test_inspection_server_worked_time() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = ConcreteInspectionServer(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=20.0)

    sf.add(job)
    env.run()

    assert server.worked_time == pytest.approx(5.0)
