from __future__ import annotations

import csv
import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import groupby

from simulatte.demand.customer_order import CustomerOrder
from simulatte.demand.shift import Shift
from simulatte.products import Product, ProductsGenerator
from simulatte.requests import LayerRequest, PalletRequest, ProductRequest


@dataclass
class CustomerOrdersGenerator:
    """Abstract factory for the generation of CustomerOrders for a simulation.

    :param n_days: number of days to be generated
    :param n_shift_per_day: number of shifts in each day
    :param n_layers: fixed number of layers in a pallet
    :param orders_per_shift_distribution: a distribution from which to sample the number of orders per shift
    :param pallet_per_order_distribution: a distribution from which to sample the number of pallets per order
    :param products_generator: a distribution from which to sample the skus
    """

    n_days: int
    n_shift_per_day: int
    n_layers: int
    orders_per_shift_distribution: Callable[[], int]
    pallet_per_order_distribution: Callable[[], int]
    products_generator: ProductsGenerator
    max_sku_per_layer: int

    def __post_init__(self) -> None:
        self._shifts: list[Shift] = []

    def generate_pallet_requests(self) -> list[PalletRequest]:
        """Method which create the list of pallets within a customer order"""

        for _ in range(self.pallet_per_order_distribution()):
            layer_requests = []
            for _ in range(self.n_layers // 2):
                layer_requests.append(self.generate_mono_sku_layer(set()))
            for _ in range(self.n_layers // 2):
                layer_requests.append(self.generate_multi_sku_layer(set()))
            yield PalletRequest(*layer_requests)

    def generate_mono_sku_layer(self, already_selected_skus: set[Product]) -> LayerRequest:
        """Generate a single layer composed of a single SKU, without selecting already selected SKUs"""
        product = self.products_generator.choose_one(exclude=already_selected_skus)
        return LayerRequest(ProductRequest(product=product, n_cases=product.cases_per_layer))

    def generate_multi_sku_layer(self, already_selected_skus: set[Product]) -> LayerRequest:
        """Generate a single layer composed of many SKUs, without selecting already selected SKUs"""
        product_requests = []
        layer_saturation = 0
        while layer_saturation < 1:
            product: Product = self.products_generator.choose_one(exclude=already_selected_skus)
            already_selected_skus.add(product)
            qty = int((product.cases_per_layer - 1) * random.random() + 1)
            sku_layer_saturation = qty / product.cases_per_layer

            if layer_saturation + sku_layer_saturation > 1:
                sku_layer_saturation = 1 - layer_saturation
                qty = int(sku_layer_saturation * product.cases_per_layer)
                qty = max(qty, 1)

            layer_saturation += sku_layer_saturation
            product_requests.append(ProductRequest(product=product, n_cases=qty))

        return LayerRequest(*product_requests)

    def __call__(self) -> Iterable[Shift]:
        """For each day and shift, generate the customer orders list"""

        if not self._shifts:
            for n_day in range(self.n_days):
                for n_shift in range(self.n_shift_per_day):
                    customer_orders: list[CustomerOrder] = []
                    for _ in range(self.orders_per_shift_distribution()):
                        pallet_requests = list(self.generate_pallet_requests())
                        order = CustomerOrder(day=n_day, shift=n_shift, pallet_requests=pallet_requests)
                        customer_orders.append(order)

                    self._shifts.append(Shift(day=n_day, shift=n_shift, customer_orders=customer_orders))

        yield from self._shifts

    def export(self, filename: str) -> None:
        """Export the generated shifts to a file"""

        csv_fields = [
            "day",
            "shift",
            "customer_order_id",
            "pallet_request_id",
            "pallet_request_for_layer_picking",
            "pallet_request_for_case_picking",
            "n_layer",
            "layer_picking",
            "case_picking",
            "product_id",
            "cases_per_layer",
            "n_cases",
        ]

        with open(filename, "w") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=csv_fields)
            writer.writeheader()
            for shift in self():
                for customer_order in shift.customer_orders:
                    customer_order_id = id(customer_order)
                    for pallet_request in customer_order.pallet_requests:
                        pallet_request_id = id(pallet_request)
                        pallet_request_for_layer_picking = int(pallet_request.is_for_layer_picking_cell)
                        pallet_request_for_case_picking = int(pallet_request.is_for_case_picking_cell)
                        for n_layer, layer_request in enumerate(pallet_request.sub_requests):
                            layer_picking = int(layer_request.has_single_product_request)
                            case_picking = int(not layer_picking)
                            for product_request in layer_request.sub_requests:
                                writer.writerow(
                                    {
                                        "day": shift.day,
                                        "shift": shift.shift,
                                        "customer_order_id": customer_order_id,
                                        "pallet_request_id": pallet_request_id,
                                        "pallet_request_for_layer_picking": pallet_request_for_layer_picking,
                                        "pallet_request_for_case_picking": pallet_request_for_case_picking,
                                        "n_layer": n_layer,
                                        "layer_picking": layer_picking,
                                        "case_picking": case_picking,
                                        "product_id": id(product_request.product),
                                        "cases_per_layer": product_request.product.cases_per_layer,
                                        "n_cases": product_request.n_cases,
                                    }
                                )

    @property
    def customer_orders(self) -> Iterable[CustomerOrder]:
        for shift in self():
            yield from shift.customer_orders

    @property
    def pallet_requests(self) -> Iterable[PalletRequest]:
        for customer_order in self.customer_orders:
            yield from customer_order.pallet_requests

    @property
    def shift_per_day(self) -> Iterable[int, Iterable[Shift]]:
        yield from groupby(self(), lambda shift: shift.day)

    def stats(self):
        products_demand = {}  # {product: {'layer': [], 'pallet': []}}

        for day, shifts_in_day in self.shift_per_day:
            product_daily_demand = {}  # {product: (layer, pallet)}

            for shift in shifts_in_day:
                for pallet_request in shift.pallet_requests:
                    for layer_request in pallet_request.sub_requests:
                        for product_request in layer_request.sub_requests:
                            product = product_request.product

                            if product.id not in product_daily_demand:
                                product_daily_demand[product.id] = [0, 0]

                            idx = 1 if pallet_request.is_for_layer_picking_cell else 0
                            product_daily_demand[product.id][idx] += product_request.n_cases

            for product_id, (layer, pallet) in product_daily_demand.items():
                if product_id not in products_demand:
                    products_demand[product_id] = {"tray": [], "pallet": []}
                products_demand[product_id]["tray"].append(layer)
                products_demand[product_id]["pallet"].append(pallet)

        return {k: products_demand[k] for k in sorted(products_demand.keys())}
