from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from simulatte.products import Product
from simulatte.unitload import Pallet
from simulatte.utils import Identifiable

if TYPE_CHECKING:
    from simulatte.picking_cell import FeedingOperation


class Request:
    product: Product
    n_cases: int

    def __init__(self) -> None:
        self.feeding_operations: list[FeedingOperation] = []


class CaseRequest(Request):
    def __init__(self, product: Product) -> None:
        super().__init__()
        self.product = product

    def __repr__(self) -> str:
        return f"CaseRequest(product={self.product})"

    @property
    def n_cases(self) -> int:
        return 1


class ProductRequest(Request):
    def __init__(self, product: Product, n_cases: int) -> None:
        if n_cases > product.cases_per_layer:
            raise ValueError("A ProductRequest cannot exceed the product cases per layer")

        super().__init__()
        self.product = product
        self.sub_requests = [CaseRequest(product=product) for _ in range(n_cases)]
        self.processed = False
        self.pallet_request: PalletRequest | None = None

    def __repr__(self) -> str:
        return f"ProductRequest(product={self.product}, n_cases={self.n_cases})"

    @property
    def n_cases(self) -> int:
        return len(self.sub_requests)


class LayerRequest(Request):
    def __init__(self, *product_requests: ProductRequest) -> None:
        super().__init__()

        n_cases_requested = sum(r.n_cases for r in product_requests)

        if n_cases_requested > sum(r.product.cases_per_layer for r in product_requests):
            raise ValueError("Overflow of cases in the LayerRequest")

        self.sub_requests = list(product_requests)
        self.pallet_request: PalletRequest | None = None

    def __repr__(self) -> str:
        return f"LayerRequest(sub_requests={self.sub_requests})"

    @property
    def processed(self) -> bool:
        return all(product_request.processed for product_request in self.sub_requests)

    @property
    def n_cases(self) -> int:
        return sum(product_request.n_cases for product_request in self.sub_requests)

    @property
    def has_single_product_request(self) -> bool:
        """
        Returns True if the LayerRequest is composed of a single ProductRequest.
        (the LayerRequest should be processed by a LayerPickingCell)
        """
        return len(self.sub_requests) == 1

    @property
    def product(self) -> Product:
        if not self.has_single_product_request:
            raise ValueError("The LayerRequest contains more than one ProductRequest")
        return self.sub_requests[0].product

    @property
    def products(self) -> Iterable[Product]:
        for product_request in self.sub_requests:
            yield product_request.product

    def add(self, *, product_request: ProductRequest) -> LayerRequest:
        self.sub_requests.append(product_request)
        return self


class PalletRequest(Request, metaclass=Identifiable):
    def __init__(self, *layer_requests: LayerRequest, wood_board=False) -> None:
        super().__init__()
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

    def assigned(self, time: int) -> None:
        self._start_time = time

    def completed(self, time: int) -> None:
        self._end_time = time

    @property
    def lead_time(self) -> float | None:
        if self._start_time is not None and self._end_time is not None:
            return self._end_time - self._start_time
