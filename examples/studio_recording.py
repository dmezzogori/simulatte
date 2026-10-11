"""Record small production and intralogistics demos for the local Studio viewer.

uv run python examples/studio_recording.py shop.simtrace --model shop
uv run python examples/studio_recording.py fleet.simtrace --model fleet
uv run --extra studio simulatte view fleet.simtrace
"""

from __future__ import annotations

import argparse
from pathlib import Path

from simulatte import Environment, Layout, Trace, TraceRecorder
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import ShopFloorKPIs
from simulatte.intralogistics import FleetKPIs, build_simple_system
from simulatte.scenario import Scenario


def record(path: Path, model: str) -> None:
    with Environment(seed=17) as env:
        TraceRecorder(env, path)
        Layout(env)
        if model == "shop":
            system = build_immediate_release_system(
                env=env, scenario=Scenario.pure_job_shop(n_servers=5), collect_time_series=True
            )
            ShopFloorKPIs(system.shop_floor).attach(env)
        else:
            fleet, _, origin, destination, _ = build_simple_system(env)
            FleetKPIs(fleet).attach(env)
            sku = next(iter(origin.inventory))
            for quantity in (5, 8, 12, 6):
                order = fleet.create_order(sku=sku, quantity=quantity, origin=origin, destination=destination)
                fleet.submit(order)
        env.run(until=120)
    trace = Trace.open(path)
    trace.check()
    assert trace.verify()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--model", choices=("shop", "fleet"), default="shop")
    args = parser.parse_args()
    record(args.path, args.model)
    print(f"Recorded {args.model}: {args.path}")
