"""Tests for job type hierarchy."""

from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.job import (
    BaseJob,
    JobType,
    ProductionJob,
    TransportJob,
    WarehouseJob,
)
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_production_job_type() -> None:
    """ProductionJob should have PRODUCTION job type."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(
        env=env,
        family="A",
        servers=[server],
        processing_times=[5],
        due_date=10,
    )
    assert job.job_type == JobType.PRODUCTION
    assert isinstance(job, BaseJob)


def test_production_job_material_requirements() -> None:
    """ProductionJob should support material requirements per operation."""
    env = Environment()
    sf = ShopFloor(env=env)
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    materials = {
        0: {"steel": 2, "bolts": 10},
        1: {"paint": 1},
    }
    job = ProductionJob(
        env=env,
        family="A",
        servers=[s1, s2],
        processing_times=[5, 3],
        due_date=20,
        material_requirements=materials,
    )

    assert job.get_materials_for_operation(0) == {"steel": 2, "bolts": 10}
    assert job.get_materials_for_operation(1) == {"paint": 1}
    assert job.get_materials_for_operation(2) == {}  # No requirements


def test_production_job_default_no_materials() -> None:
    """ProductionJob without material_requirements should have empty dict."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(
        env=env,
        family="A",
        servers=[server],
        processing_times=[5],
        due_date=10,
    )
    assert job.material_requirements == {}
    assert job.get_materials_for_operation(0) == {}


def test_transport_job_type() -> None:
    """TransportJob should have TRANSPORT job type."""
    env = Environment()
    sf = ShopFloor(env=env)
    origin = Server(env=env, capacity=1, shopfloor=sf)
    destination = Server(env=env, capacity=1, shopfloor=sf)

    job = TransportJob(
        env=env,
        origin=origin,
        destination=destination,
        cargo={"steel": 5},
    )

    assert job.job_type == JobType.TRANSPORT
    assert job.origin is origin
    assert job.destination is destination
    assert job.cargo == {"steel": 5}
    assert job.family == "transport"
    assert isinstance(job, BaseJob)


def test_warehouse_job_pick() -> None:
    """WarehouseJob should support pick operations."""
    env = Environment()
    sf = ShopFloor(env=env)
    warehouse = Server(env=env, capacity=2, shopfloor=sf)

    job = WarehouseJob(
        env=env,
        warehouse=warehouse,
        product="steel",
        quantity=5,
        operation_type="pick",
        processing_time=2.0,
    )

    assert job.job_type == JobType.WAREHOUSE
    assert job.product == "steel"
    assert job.quantity == 5
    assert job.operation_type == "pick"
    assert job.family == "warehouse_pick"
    assert job.processing_times == (2.0,)
    assert isinstance(job, BaseJob)


def test_warehouse_job_put() -> None:
    """WarehouseJob should support put operations."""
    env = Environment()
    sf = ShopFloor(env=env)
    warehouse = Server(env=env, capacity=2, shopfloor=sf)

    job = WarehouseJob(
        env=env,
        warehouse=warehouse,
        product="bolts",
        quantity=100,
        operation_type="put",
    )

    assert job.operation_type == "put"
    assert job.family == "warehouse_put"


def test_warehouse_job_invalid_operation() -> None:
    """WarehouseJob should reject invalid operation types."""
    env = Environment()
    sf = ShopFloor(env=env)
    warehouse = Server(env=env, capacity=2, shopfloor=sf)

    with pytest.raises(ValueError, match="operation_type must be 'pick' or 'put'"):
        WarehouseJob(
            env=env,
            warehouse=warehouse,
            product="steel",
            quantity=5,
            operation_type="move",
        )


def test_base_job_is_abstract() -> None:
    """BaseJob should not be directly instantiable."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    with pytest.raises(TypeError, match="abstract"):
        BaseJob(  # type: ignore[abstract]
            env=env,
            job_type=JobType.PRODUCTION,
            family="A",
            servers=[server],
            processing_times=[5],
            due_date=10,
        )


def test_job_repr() -> None:
    """Each job type should have appropriate repr."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    prod_job = ProductionJob(
        env=env,
        family="A",
        servers=[server],
        processing_times=[5],
        due_date=10,
    )
    assert "ProductionJob" in repr(prod_job)
    assert prod_job.id in repr(prod_job)

    transport_job = TransportJob(
        env=env,
        origin=server,
        destination=server,
        cargo={"x": 1},
    )
    assert "TransportJob" in repr(transport_job)
    assert "cargo" in repr(transport_job)

    warehouse_job = WarehouseJob(
        env=env,
        warehouse=server,
        product="y",
        quantity=2,
        operation_type="pick",
    )
    assert "WarehouseJob" in repr(warehouse_job)
    assert "pick" in repr(warehouse_job)
    assert "2x y" in repr(warehouse_job)
