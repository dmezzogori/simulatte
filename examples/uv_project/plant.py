"""Immediate-release shop used to exercise remote workers; imports one helper from the local ``shopkit`` package."""

from dataclasses import dataclass
from typing import Annotated

from shopkit import scenario_servers
from simulatte import Range, model
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import ShopFloorKPIs
from simulatte.scenario import Scenario


@dataclass(frozen=True)
class Params:
    servers: Annotated[int, Range(1, 16)] = 5


@model(params=Params, horizon=60)
def build(env, layout, params):
    """Immediate-release production shop."""
    system = build_immediate_release_system(
        env=env, scenario=Scenario.pure_job_shop(n_servers=scenario_servers(params.servers))
    )
    ShopFloorKPIs(system.shop_floor).attach(env)
