from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.sku import SKU
from simulatte.intralogistics.warehouse import Warehouse


class TestWarehouse:
    def _make_warehouse(self, env: Environment) -> tuple[Warehouse, list[Node]]:
        in_bay = Node(id="IN", x=0.0, y=0.0)
        out_bay = Node(id="OUT", x=1.0, y=0.0)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)
        bolts = SKU(id="BOLTS", weight=0.1, volume=0.01)
        wh = Warehouse(
            env=env,
            name="WH-A",
            input_bays=[in_bay],
            output_bays=[out_bay],
            n_slots=2,
            products=[steel, bolts],
            initial_inventory={steel: 100, bolts: 500},
            pick_time_fn=lambda sku, qty: 2.0,
            put_time_fn=lambda sku, qty: 1.0,
        )
        return wh, [in_bay, out_bay]

    def test_creation(self, env: Environment) -> None:
        wh, nodes = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)
        assert wh.name == "WH-A"
        assert wh.get_inventory_level(steel) == 100

    def test_pick_success(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)

        def do_pick():
            yield from wh.pick(steel, 10)

        env.process(do_pick())
        env.run()
        assert wh.get_inventory_level(steel) == 90
        assert wh.total_picks == 1
        assert env.now == pytest.approx(2.0)

    def test_pick_blocks_on_empty(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)
        events: list[tuple[str, float]] = []

        def do_pick():
            yield from wh.pick(steel, 200)
            events.append(("picked", env.now))

        def restock():
            yield env.timeout(5.0)
            yield from wh.put(steel, 200)
            events.append(("restocked", env.now))

        env.process(do_pick())
        env.process(restock())
        env.run()

        assert events[0] == ("restocked", pytest.approx(6.0))
        assert events[1] == ("picked", pytest.approx(8.0))

    def test_put(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)

        def do_put():
            yield from wh.put(steel, 50)

        env.process(do_put())
        env.run()
        assert wh.get_inventory_level(steel) == 150
        assert wh.total_puts == 1

    def test_pick_does_not_deadlock_with_put(self, env: Environment) -> None:
        """Critical test: pick waits for inventory without holding a slot,
        so put can always get a slot to restock."""
        in_bay = Node(id="IN", x=0.0, y=0.0)
        out_bay = Node(id="OUT", x=1.0, y=0.0)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)
        wh = Warehouse(
            env=env,
            name="WH",
            input_bays=[in_bay],
            output_bays=[out_bay],
            n_slots=1,
            products=[steel],
            initial_inventory={steel: 0},
            pick_time_fn=lambda sku, qty: 1.0,
            put_time_fn=lambda sku, qty: 1.0,
        )
        completed: list[str] = []

        def pick_task():
            yield from wh.pick(steel, 10)
            completed.append("pick")

        def put_task():
            yield env.timeout(1.0)
            yield from wh.put(steel, 10)
            completed.append("put")

        env.process(pick_task())
        env.process(put_task())
        env.run()

        assert "put" in completed
        assert "pick" in completed

    def test_nearest_output_bay_uses_graph_distance(self, env: Environment) -> None:
        """OUT2 is Euclidean-closer to AGV but only reachable via a long detour."""
        out1 = Node(id="OUT1", x=0.0, y=0.0)
        out2 = Node(id="OUT2", x=9.0, y=0.0)
        detour = Node(id="D", x=8.0, y=5.0)
        agv_pos = Node(id="AGV", x=8.0, y=0.0)
        steel = SKU(id="S", weight=1.0, volume=0.1)
        arcs = [
            Arc(source=agv_pos, target=out1),
            Arc(source=agv_pos, target=detour),
            Arc(source=detour, target=out2),
        ]
        graph = LayoutGraph([out1, out2, detour, agv_pos], arcs)
        wh = Warehouse(
            env=env,
            name="WH",
            input_bays=[],
            output_bays=[out1, out2],
            n_slots=1,
            products=[steel],
            pick_time_fn=lambda s, q: 1.0,
            put_time_fn=lambda s, q: 1.0,
        )
        nearest = wh.nearest_output_bay(agv_pos, graph)
        assert nearest == out1  # graph distance: AGV→OUT1 = 8.0, AGV→D→OUT2 ≈ 5+5.1 = 10.1

    def test_metrics(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        steel = SKU(id="STEEL", weight=10.0, volume=0.5)

        def ops():
            yield from wh.pick(steel, 5)
            yield from wh.pick(steel, 5)
            yield from wh.put(steel, 5)

        env.process(ops())
        env.run()
        assert wh.total_picks == 2
        assert wh.total_puts == 1
        assert wh.average_pick_time == pytest.approx(2.0)
        assert wh.average_put_time == pytest.approx(1.0)

    def test_repr(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        assert "WH-A" in repr(wh)

    def test_get_inventory_level_unknown_sku(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        unknown = SKU(id="UNKNOWN", weight=1.0, volume=0.1)
        with pytest.raises(KeyError, match="Unknown product"):
            wh.get_inventory_level(unknown)

    def test_pick_unknown_sku(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        unknown = SKU(id="UNKNOWN", weight=1.0, volume=0.1)
        with pytest.raises(KeyError, match="Unknown product"):
            gen = wh.pick(unknown, 1)
            next(gen)

    def test_put_unknown_sku(self, env: Environment) -> None:
        wh, _ = self._make_warehouse(env)
        unknown = SKU(id="UNKNOWN", weight=1.0, volume=0.1)
        with pytest.raises(KeyError, match="Unknown product"):
            gen = wh.put(unknown, 1)
            next(gen)
