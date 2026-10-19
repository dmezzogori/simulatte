"""Numerical AGV state intervals without requiring animation recording."""

from dataclasses import dataclass
from simulatte import model
from simulatte.analysis import MeasurementPlan, Population
from simulatte.analysis.builtins import AgvDurationCollector
from simulatte.intralogistics import build_simple_system


POPULATIONS = (Population("all_vehicles", lambda row: True, "interval_start"),)


def measurements():
    return AgvDurationCollector(
        fields={"family": ("string", lambda agv: agv.agv_type.name)},
        populations=POPULATIONS,
    )


PLAN = MeasurementPlan("durations", (measurements().schema,), POPULATIONS)


@dataclass(frozen=True)
class Params:
    vehicles: int = 2


@model(params=Params, horizon=100, measurement_plans=(PLAN,))
def build(env, layout, params):
    fleet, _, origin, destination, _ = build_simple_system(env, n_agvs=params.vehicles)
    measurements().attach(env)
    for sku in origin.inventory:
        fleet.submit(fleet.create_order(sku=sku, quantity=1, origin=origin, destination=destination))
