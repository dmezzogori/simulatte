from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .unitload import Pallet
from .utils import Identifiable

if TYPE_CHECKING:
    from collections.abc import Iterable

    from simulatte.products import Product


class Request:
    pass


class CaseRequest(Request):
    def __init__(self, product: Product) -> None:
        self.product = product
        self.n_cases = 1
        self.picked_n_cases = 0

    def remaining_to_pick_n_cases(self) -> int:
        return self.n_cases - self.picked_n_cases


class ProductRequest(Request):
    def __init__(self, product: Product, n_cases: int) -> None:
        if n_cases > product.cases_per_layer:
            raise ValueError("A ProductRequest cannot exceed the product cases per layer")

        self.product = product
        self.n_cases = n_cases
        self.picked_n_cases = 0
        self.sub_requests = tuple(CaseRequest(product=product) for _ in range(n_cases))
        self.pallet_request: PalletRequest | None = None

    @property
    def processed(self) -> bool:
        return self.picked_n_cases == self.n_cases

    @property
    def remaining_to_pick_n_cases(self) -> int:
        return self.n_cases - self.picked_n_cases


class LayerRequest(Request):
    def __init__(self, *product_requests: ProductRequest) -> None:
        self.sub_requests = tuple(product_requests)

        if self.n_cases > sum(r.product.cases_per_layer for r in product_requests):
            raise ValueError("Overflow of cases in the LayerRequest")

        self.pallet_request: PalletRequest | None = None

    @property
    def products(self) -> Iterable[Product]:
        for product_request in self.sub_requests:
            yield product_request.product

    @property
    def product(self) -> Product:
        if not self.has_single_product_request:
            raise ValueError("The LayerRequest contains more than one ProductRequest")
        return self.sub_requests[0].product

    @property
    def n_cases(self) -> int:
        """
        Returns the number of cases in the LayerRequest.
        """

        return sum(product_request.n_cases for product_request in self.sub_requests)

    @property
    def picked_n_cases(self) -> int:
        """
        Returns the number of cases picked in the LayerRequest.
        """

        return sum(product_request.picked_n_cases for product_request in self.sub_requests)

    @property
    def remaining_to_pick_n_cases(self) -> int:
        """
        Returns the number of cases remaining to pick in the LayerRequest.
        """

        return self.n_cases - self.picked_n_cases

    @property
    def processed(self) -> bool:
        """
        Returns True if the LayerRequest is completely processed,
        i.e. when all the ProductRequests in the LayerRequest are processed.
        """

        return all(product_request.processed for product_request in self.sub_requests)

    @property
    def has_single_product_request(self) -> bool:
        """
        Returns True if the LayerRequest is composed of a single ProductRequest.
        (the LayerRequest should be processed by a LayerPickingCell)
        """

        return len(self.sub_requests) == 1

    def total_workload(self, what: Literal["layers", "cases"]):
        """
        Returns the total workload of the LayerRequest.

        The unit of measure can be layers or cases.
        If the workload is expressed in terms of layers, the workload is equal to 1 (one layer).
        If the workload is expressed in terms of cases, the workload is equal
        to the number of cases in the LayerRequest.
        """

        if what == "layers":
            return 1
        elif what == "cases":
            return self.n_cases
        else:
            raise ValueError(f"Invalid workload type: {what}")

    def remaining_workload(self, what: Literal["layers", "cases"]):
        """
        Returns the remaining workload of the LayerRequest.

        The unit of measure can be layers or cases.
        If the workload is expressed in terms of layers, the workload is either 1 or 0.
        If the workload is expressed in terms of cases, the workload is equal
        to the number of cases remaining to pick in the LayerRequest.
        """

        if what == "layers":
            return self.total_workload("layers") - int(self.processed)
        elif what == "cases":
            return self.total_workload("cases") - self.picked_n_cases
        else:
            raise ValueError(f"Invalid workload type: {what}")


class PalletRequest(Request, metaclass=Identifiable):
    def __init__(self, *layer_requests: LayerRequest, wood_board=False) -> None:
        self.sub_requests = list(layer_requests)
        self.unit_load = Pallet(wood_board=wood_board)

        for layer_request in self.sub_requests:
            layer_request.pallet_request = self
            for product_request in layer_request.sub_requests:
                product_request.pallet_request = self

        self._start_time = None
        self._end_time = None

    def __repr__(self):
        return f"PalletRequest(id={self.id})"

    def __iter__(self) -> Iterable[LayerRequest]:
        return (layer_request for layer_request in self.sub_requests if not layer_request.processed)

    @property
    def n_layers(self) -> int:
        return len(self.sub_requests)

    @property
    def n_cases(self) -> int:
        return sum(layer_request.n_cases for layer_request in self.sub_requests)

    @property
    def products(self) -> Iterable[Product]:
        for layer_request in self.sub_requests:
            yield from layer_request.products

    @property
    def is_for_layer_picking_cell(self) -> bool:
        """
        Returns True if the PalletRequest has to be processed *ONLY* by a LayerPickingCell.
        (all LayerRequests are composed of **one** ProductRequest)
        """
        return all(layer_request.has_single_product_request for layer_request in self.sub_requests)

    @property
    def is_for_case_picking_cell(self) -> bool:
        """
        Returns True if the PalletRequest has to be processed *ONLY* by a CasePickingCell.
        (all LayerRequests are composed of **more than one** ProductRequest)
        """
        return all(not layer_request.has_single_product_request for layer_request in self.sub_requests)

    @property
    def lead_time(self) -> float | None:
        if self._start_time is not None and self._end_time is not None:
            return self._end_time - self._start_time

    def total_workload(self, what: Literal["layers", "cases"]) -> int:
        """
        Returns the total workload of the PalletRequest.

        The unit of measure can be layers or cases.
        If the workload is expressed in terms of layers, the workload is equal
        to the number of layers in the PalletRequest.
        If the workload is expressed in terms of cases, the workload is equal
        to the number of cases in the PalletRequest.
        """

        if what == "layer":
            return sum(layer_request.total_workload("layers") for layer_request in self.sub_requests)
        elif what == "cases":
            return sum(layer_request.total_workload("cases") for layer_request in self.sub_requests)
        else:
            raise ValueError(f"Invalid workload type: {what}")

    def remaining_workload(self, what: Literal["layers", "cases"]) -> int:
        """
        Returns the remaining workload of the PalletRequest.

        The unit of measure can be layers or cases.
        If the workload is expressed in terms of layers, the workload is equal
        to the number of layers remaining to pick in the PalletRequest.
        If the workload is expressed in terms of cases, the workload is equal
        to the number of cases remaining to pick in the PalletRequest.
        """

        if what == "layers":
            return sum(layer_request.remaining_workload("layers") for layer_request in self.sub_requests)
        elif what == "cases":
            return sum(layer_request.remaining_workload("cases") for layer_request in self.sub_requests)
        else:
            raise ValueError(f"Invalid workload type: {what}")

    def assigned(self, time: int) -> None:
        self._start_time = time

    def completed(self, time: int) -> None:
        self._end_time = time
