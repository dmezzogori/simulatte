"""Numerical job outcomes; callable directly from ordinary Python."""

from dataclasses import dataclass
import json
from pathlib import Path

from simulatte import model
from simulatte.analysis import MeasurementPlan, Population
from simulatte.analysis.builtins import JobOutcomeCollector
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


POPULATIONS = (Population("family_A", lambda row: row["family"] == "A", "arrival", fields=("family",)),)


def measurements():
    return JobOutcomeCollector(
        fields={"family": ("string", lambda job: job.sku)},
        populations=POPULATIONS,
        membership_at="arrival",
    )


PLAN = MeasurementPlan("outcomes", (measurements().schema,), POPULATIONS)


@dataclass(frozen=True)
class Params:
    speed: float = 1.0


@model(params=Params, horizon=30, warmup=2, inputs=("data/jobs.json",), measurement_plans=(PLAN,))
def build(env, layout, params):
    if params.speed <= 0:
        raise ValueError("speed must be positive")
    floor = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=floor)
    measurements().attach(env)
    jobs = json.loads((Path(__file__).parents[1] / "data/jobs.json").read_text())
    for item in jobs:
        job = ProductionJob(
            env=env,
            sku=item["family"],
            servers=[server],
            processing_times=[item["processing"] / params.speed],
            due_date=item["due"],
        )
        floor.add(job)
