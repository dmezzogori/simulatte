"""A typed production model usable through the CLI or as an ordinary script.

uv run simulatte run examples/studio_model.py --param servers=3 --seed 17
uv run python examples/studio_model.py
"""

from dataclasses import dataclass
from typing import Annotated

from simulatte import Environment, Layout, Range, model
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import ShopFloorKPIs
from simulatte.scenario import Scenario


@dataclass(frozen=True)
class Params:
    servers: Annotated[int, Range(1, 16)] = 5


@model(params=Params, horizon=120)
def build(env, layout, params):
    """Immediate-release production shop."""
    system = build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=params.servers))
    ShopFloorKPIs(system.shop_floor).attach(env)


if __name__ == "__main__":
    with Environment(seed=17) as environment:
        build(environment, Layout(environment), Params())
        environment.run(until=120)
    print("Completed ordinary script run")
