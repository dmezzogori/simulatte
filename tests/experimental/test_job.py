"""Tests for experimental job types (TransportJob, WarehouseJob)."""

from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.experimental import TransportJob, WarehouseJob
from simulatte.job import BaseJob, JobType
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


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
    assert job.sku == "transport"
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
    assert job.sku == "warehouse_pick"
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
    assert job.sku == "warehouse_put"


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


def test_transport_job_repr() -> None:
    """TransportJob should have appropriate repr."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    transport_job = TransportJob(
        env=env,
        origin=server,
        destination=server,
        cargo={"x": 1},
    )
    assert "TransportJob" in repr(transport_job)
    assert "cargo" in repr(transport_job)


def test_warehouse_job_repr() -> None:
    """WarehouseJob should have appropriate repr."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

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
