"""A file-layer hall width controls staged machine creation.

Run with: uv run --all-extras python examples/layout_stages.py
File patches are plain data here; Layout(env, file="layout.json") loads the same
schema from disk. Entries for machines removed by a smaller width stay in the
file layer, ready to be reused if the hall grows again.
"""

from __future__ import annotations

from typing import ClassVar

from simulatte import Environment, Layout
from simulatte.entities import Entity, StateSchema
from simulatte.layout import Footprint, Point
from simulatte.server import Server


class Hall(Entity, kind="layout_example_hall"):
    state_schema: ClassVar[StateSchema] = StateSchema({})


def main() -> None:
    for width in (12, 6):
        # File values override the code width of 12 below. List four authored
        # machine entries in both cases so the smaller hall leaves two orphans.
        file_layer = {
            "schema_version": "1.0",
            "placements": {
                "hall": {"kind": "layout_example_hall", "footprint": {"width": width}},
                **{f"machine-{i:02}": {"kind": "server", "visual": {"color": "#5b7c99"}} for i in range(4)},
            },
        }
        with Environment() as env:
            layout = Layout.from_dict(env, file_layer)
            hall = Hall()
            env.entities.attach(hall, name="hall")
            layout.place(hall, position=Point(6, 0), footprint=Footprint(12, 3))
            width_ref = layout.ref("placements", "hall", "footprint", "width")
            first_position = layout.ref("placements", "machine-00", "position")
            machines: list[Server] = []
            observed_positions = []

            @layout.stage("machines", reads=[width_ref], after=["declare"])
            def create_machines(ctx):
                # Only declared reads of prior-stage explicit values are legal.
                # Auto positions cannot determine model topology or counts.
                count = int(ctx.physical(width_ref) / 3)
                for i in range(count):
                    machine = Server(env=ctx.env, capacity=1, name=f"machine-{i:02}")
                    layout.place(machine, position=Point(1.5 + 3 * i, 0))
                    machines.append(machine)

            @layout.stage("inspect", reads=[first_position], after=["machines"])
            def inspect(ctx):
                # `after` makes machine-00 prior-stage data; `reads` grants
                # access. Neither lets this stage edit its frozen placement.
                observed_positions.append(ctx.physical(first_position))

            layout.prepare()
            attached = {entity.id for entity in env.entities.live()}
            orphans = sorted(set(layout.to_dict()["placements"]) - attached)
            print(f"File width: {width} m; machines: {len(machines)}; orphan placements: {len(orphans)}")
            print(f"First machine position after machines stage: {tuple(observed_positions[0])}")
            print(f"Preserved orphan ids: {', '.join(orphans) or '(none)'}")
            print(f"Prepared at time: {env.now}")


if __name__ == "__main__":
    main()
