from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import simpy

from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import Deltas
from simulatte.intralogistics._resources import NotifyingContainer, NotifyingResource
from simulatte.intralogistics.events import WarehouseInventoryChanged, WarehouseSlotChanged

if TYPE_CHECKING:
    from collections.abc import Callable

    from simpy.events import ProcessGenerator
    from simpy.resources.container import ContainerAmount
    from simpy.resources.resource import Request

    from simulatte.environment import Environment
    from simulatte.intralogistics.graph import LayoutGraph, Node
    from simulatte.intralogistics.sku import SKU


class Warehouse(Entity, kind="warehouse"):
    """A storage facility with per-SKU inventory and finite pick/put slots; its id is ``name``.

    Events (spec §6.4): ``warehouse.inventory_changed`` whenever an inventory container completes a put or a get,
    and ``warehouse.slot_changed`` whenever a pick or put slot is acquired or released.
    """

    state_schema: ClassVar[StateSchema] = StateSchema(
        {"inventory": FieldSpec("float", collection="map"), "slots_in_use": FieldSpec("int")}
    )

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
        label: str | None = None,
    ) -> None:
        self.env = env
        self.name = name
        self.input_bays = list(input_bays)
        self.output_bays = list(output_bays)
        self.pick_time_fn = pick_time_fn
        self.put_time_fn = put_time_fn
        self._slots = NotifyingResource(env, capacity=n_slots, on_change=self._slot_changed)

        initial = initial_inventory or {}
        self.inventory: dict[SKU, simpy.Container] = {
            product: NotifyingContainer(
                env,
                capacity=float("inf"),
                init=initial.get(product, 0),
                on_change=self._inventory_reporter(product),
            )
            for product in products
        }

        self.total_picks: int = 0
        self.total_puts: int = 0
        self._total_pick_time: float = 0.0
        self._total_put_time: float = 0.0
        env.entities.attach(self, name=name, label=label)

    def snapshot(self) -> dict[str, Any]:
        """Current entity state: inventory level per SKU id and the number of slots in use."""
        return {
            "inventory": {sku.id: float(container.level) for sku, container in self.inventory.items()},
            "slots_in_use": self._slots.count,
            "label": self.label,
        }

    def _inventory_reporter(self, sku: SKU) -> Callable[[NotifyingContainer, ContainerAmount], None]:
        """The change callback of the container of `sku`: emits ``warehouse.inventory_changed``."""

        def changed(container: NotifyingContainer, amount: ContainerAmount) -> None:
            env = self.env
            if env.wants(WarehouseInventoryChanged):
                level = float(container.level)
                env.emit(
                    WarehouseInventoryChanged(
                        warehouse=self.id,
                        sku=sku.id,
                        level=level,
                        delta=float(amount),
                        deltas=Deltas.build().put(self.id, "inventory", sku.id, level).done(),
                    )
                )

        return changed

    def _slot_changed(self, request: Request, granted: bool) -> None:
        """Emit ``warehouse.slot_changed`` after a slot was acquired or released."""
        env = self.env
        if env.wants(WarehouseSlotChanged):
            in_use = self._slots.count
            env.emit(
                WarehouseSlotChanged(
                    warehouse=self.id,
                    in_use=in_use,
                    deltas=Deltas.build().set(self.id, "slots_in_use", in_use).done(),
                )
            )

    def get_inventory_level(self, sku: SKU) -> float:
        if sku not in self.inventory:
            raise KeyError(f"Unknown product: {sku.id}")
        return self.inventory[sku].level

    def pick(self, sku: SKU, quantity: int, *, on_committed: Callable[[], None] | None = None) -> ProcessGenerator:
        if sku not in self.inventory:
            raise KeyError(f"Unknown product: {sku.id}")
        # Wait for inventory FIRST (no slot held — prevents deadlock with put)
        get_event = self.inventory[sku].get(quantity)
        try:
            yield get_event
        except simpy.Interrupt:
            if not get_event.triggered:
                get_event.cancel()
            raise
        if on_committed is not None:
            on_committed()
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
            return graph.path_distance(path)

        return min(bays, key=_graph_distance)

    @property
    def average_pick_time(self) -> float:
        return self._total_pick_time / self.total_picks if self.total_picks > 0 else 0.0

    @property
    def average_put_time(self) -> float:
        return self._total_put_time / self.total_puts if self.total_puts > 0 else 0.0

    def __repr__(self) -> str:
        return f"Warehouse(name={self.name!r})"
