from __future__ import annotations

import pytest
from typing import cast

from simulatte.demand.customer_order import CustomerOrder
from simulatte.demand.shift import Shift
from simulatte.distance.distance import Distance
from simulatte.location import Location
from simulatte.protocols.job import Job
from simulatte.controllers.system_controller import SystemController


def test_shift_jobs_property_flattens_orders():
    jobs_a = (cast(Job, "job-a1"), cast(Job, "job-a2"))
    jobs_b = (cast(Job, "job-b1"),)
    shift = Shift(
        day=1,
        shift=2,
        customer_orders=(
            CustomerOrder(day=1, shift=2, jobs=jobs_a),
            CustomerOrder(day=1, shift=2, jobs=jobs_b),
        ),
    )

    assert list(shift.jobs) == ["job-a1", "job-a2", "job-b1"]


def test_distance_as_distance_not_implemented():
    loc_a = Location()
    loc_b = Location()
    distance = Distance(system=cast(SystemController, object()), from_=loc_a, to=loc_b)

    with pytest.raises(NotImplementedError):
        _ = distance.as_distance
