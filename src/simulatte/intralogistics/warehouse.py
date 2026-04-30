from __future__ import annotations

import math
from typing import TYPE_CHECKING

import simpy

if TYPE_CHECKING:
    from collections.abc import Callable

    from simpy.events import ProcessGenerator

    from simulatte.environment import Environment
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.sku import SKU


class Warehouse:
    def __init__(
        self,
        *,
        env: Environment,
        name: str,
        input_bays: list[Node],
        output_bays: list[Node],
        n_slots: int,
        products: list[SKU],
        initial_inventory: dict[SKU, int] | None = None,
        pick_time_fn: Callable[[SKU, int], float],
        put_time_fn: Callable[[SKU, int], float],
    ) -> None:
        self.env = env
        self.name = name
        self.input_bays = list(input_bays)
        self.output_bays = list(output_bays)
        self.pick_time_fn = pick_time_fn
        self.put_time_fn = put_time_fn
        self._slots = simpy.Resource(env, capacity=n_slots)

        initial = initial_inventory or {}
        self.inventory: dict[SKU, simpy.Container] = {
            product: simpy.Container(env, capacity=float("inf"), init=initial.get(product, 0))
            for product in products
        }

        self.total_picks: int = 0
        self.total_puts: int = 0
        self._total_pick_time: float = 0.0
        self._total_put_time: float = 0.0

    def get_inventory_level(self, sku: SKU) -> float:
        if sku not in self.inventory:
            raise KeyError(f"Unknown product: {sku.id}")
        return self.inventory[sku].level

    def pick(self, sku: SKU, quantity: int) -> ProcessGenerator:
        if sku not in self.inventory:
            raise KeyError(f"Unknown product: {sku.id}")
        # Wait for inventory FIRST (no slot held — prevents deadlock with put)
        yield self.inventory[sku].get(quantity)
        # Then acquire a slot for the physical pick operation
        with self._slots.request() as req:
            yield req
            pick_time = self.pick_time_fn(sku, quantity)
            yield self.env.timeout(pick_time)
            self.total_picks += 1
            self._total_pick_time += pick_time

    def put(self, sku: SKU, quantity: int) -> ProcessGenerator:
        if sku not in self.inventory:
            raise KeyError(f"Unknown product: {sku.id}")
        with self._slots.request() as req:
            yield req
            put_time = self.put_time_fn(sku, quantity)
            yield self.env.timeout(put_time)
            yield self.inventory[sku].put(quantity)
            self.total_puts += 1
            self._total_put_time += put_time

    def nearest_input_bay(self, from_node: Node, graph: LayoutGraph) -> Node:
        return self._nearest_bay(from_node, self.input_bays, graph)

    def nearest_output_bay(self, from_node: Node, graph: LayoutGraph) -> Node:
        return self._nearest_bay(from_node, self.output_bays, graph)

    @staticmethod
    def _nearest_bay(from_node: Node, bays: list[Node], graph: LayoutGraph) -> Node:
        def _graph_distance(bay: Node) -> float:
            path = graph.shortest_path(from_node, bay)
            if path is None:
                return float("inf")
            return sum(
                math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y)
                for i in range(len(path) - 1)
            )
        return min(bays, key=_graph_distance)

    @property
    def average_pick_time(self) -> float:
        return self._total_pick_time / self.total_picks if self.total_picks > 0 else 0.0

    @property
    def average_put_time(self) -> float:
        return self._total_put_time / self.total_puts if self.total_puts > 0 else 0.0

    def __repr__(self) -> str:
        return f"Warehouse(name={self.name!r})"
