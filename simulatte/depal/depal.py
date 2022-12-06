from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.location import Location
from simulatte.resources.store import Store
from simulatte.simpy_extension import SequentialStore
from simulatte.typings import ProcessGenerator
from simulatte.utils.utils import as_process

if TYPE_CHECKING:
    from simulatte.products import Product
    from simulatte.stores import WarehouseStore
    from simulatte.unitload import Pallet, Tray


class DepalOperation:
    def __init__(self, *, product: Product, pallet: Pallet, store: WarehouseStore):
        self.product = product
        self.pallet = pallet
        self.store = store


class Depal:
    def __init__(
        self,
        *,
        env: Environment,
        input_queue: Store[DepalOperation],
        output_queue: SequentialStore[Tray],
        processing_timeout: int,
    ) -> None:
        self.env = env
        self.processing_timeout = processing_timeout

        self.input_location = Location(name="DepalInputLocation")
        self.output_location = Location(name="DepalOutputLocation")

        self.input_queue = input_queue
        self.output_queue = output_queue

    @as_process
    def get(self, *, layer) -> ProcessGenerator:
        yield self.output_queue.get(lambda item: item == layer)

    @as_process
    def put(self, *, depal_operation: DepalOperation) -> ProcessGenerator:
        yield self.input_queue.put(depal_operation)

    @as_process
    def main(self) -> ProcessGenerator:
        while True:
            depal_operation: DepalOperation = yield self.input_queue.get()

            for layer in depal_operation.pallet.layers:
                yield self.env.timeout(self.processing_timeout)
                yield self.output_queue.put(layer)
