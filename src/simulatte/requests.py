from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, TypeVar

from simulatte.operations import FeedingOperation
from simulatte.unitload.pallet import PalletMultiProduct
from simulatte.utils import EnvMixin, IdentifiableMixin, as_process

if TYPE_CHECKING:
    from simulatte.products import Product


class PickingRequestMixin(IdentifiableMixin, EnvMixin):
    feeding_operations: list[FeedingOperation]

    def __init__(self) -> None:
        IdentifiableMixin.__init__(self)
        EnvMixin.__init__(self)

        self._start_time = None
        self._end_time = None

        self.sub_jobs = []
        self.parent = None
        self.prev = None
        self.next = None

        self.workload = 0
        self.remaining_workload = 0
        self.n_cases = 0

    def __iter__(self):
        return iter(self.sub_jobs)

    def __enter__(self) -> PickingRequestMixin:
        self.started()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.completed()
        self.remaining_workload -= self.workload

    @property
    def lead_time(self) -> float | None:
        if self._start_time is not None and self._end_time is not None:
            return self._end_time - self._start_time
        return None

    def _register_sub_jobs(self, *sub_jobs: PickingRequestMixin) -> None:
        self.sub_jobs = tuple(sub_jobs)
        for sub_job in self.sub_jobs:
            sub_job.parent = self

    def started(self) -> None:
        """Mark as started"""

        self._start_time = self.env.now

    def completed(self) -> None:
        """Mark as completed"""

        self._end_time = self.env.now


class CaseRequest(PickingRequestMixin):
    parent: ProductRequest
    sub_jobs: None

    def __init__(self, product: Product) -> None:
        super().__init__()
        self.sub_jobs = None

        self.product = product

        self.workload = 1
        self.remaining_workload = 1
        self.n_cases = 1

        self.feeding_operations = []


class ProductRequest(PickingRequestMixin):
    parent: LayerRequest
    sub_jobs: tuple[CaseRequest, ...]
    product: Product

    def __init__(self, product: Product, n_cases: int) -> None:
        if n_cases > product.cases_per_layer:
            raise ValueError("A ProductRequest cannot exceed the product cases per layer")

        super().__init__()
        self.product = product
        self.n_cases = n_cases

        self.sub_jobs = tuple(CaseRequest(product=product) for _ in range(n_cases))
        for case_request in self.sub_jobs:
            case_request.parent = self

        self.workload = n_cases
        self.remaining_workload = n_cases

        self.feeding_operations = []

    @as_process
    def iter_feeding_operations(self) -> Iterable[FeedingOperation]:
        while not self.feeding_operations:
            yield self.env.timeout(1)

        return self.feeding_operations


class LayerRequest(PickingRequestMixin):
    parent: PalletRequest
    sub_jobs: tuple[ProductRequest, ...]

    def __init__(self, *product_requests: ProductRequest) -> None:
        super().__init__()

        self.sub_jobs = tuple(product_requests)
        for product_request in self.sub_jobs:
            product_request.parent = self

        self.n_cases = sum(product_request.n_cases for product_request in self.sub_jobs)
        self.workload = 1
        self.remaining_workload = 1


class LayerRequestSingleProduct(LayerRequest):
    parent: PalletRequest
    sub_jobs: tuple[ProductRequest]
    product: Product

    def __init__(self, *products_requests: ProductRequest) -> None:
        super().__init__(*products_requests)
        self.product = self.sub_jobs[0].product


class LayerRequirementMultiProduct(LayerRequest):
    parent: PalletRequest
    sub_jobs: tuple[ProductRequest, ...]
    products: Sequence[Product]

    def __init__(self, *products_requests: ProductRequest) -> None:
        super().__init__(*products_requests)
        self.products = tuple(product_request.product for product_request in self.sub_jobs)


_L = TypeVar("_L", bound=LayerRequest)


class PalletRequest(PickingRequestMixin):
    parent: None
    unit_load: PalletMultiProduct
    sub_jobs: tuple[LayerRequest, ...]

    def __init__(self, *layer_requests: _L) -> None:
        super().__init__()
        self._register_sub_jobs(*layer_requests)

        self.n_cases = sum(layer_request.n_cases for layer_request in self.sub_jobs)
        self.unit_load = PalletMultiProduct()

        self.workload = len(self.sub_jobs)
        self.remaining_workload = len(self.sub_jobs)

    @property
    def feeding_operations(self) -> tuple[FeedingOperation, ...]:
        return tuple(
            feeding_operation
            for layer_request in self.sub_jobs
            for product_request in layer_request.sub_jobs
            for feeding_operation in product_request.feeding_operations
        )

    @property
    def oos_delay(self):
        fos = self.feeding_operations
        delay = 0
        i = 1
        j = 0
        while i < len(fos):
            t1, t2 = fos[j].log.finished_agv_trip_to_cell, fos[i].log.finished_agv_trip_to_cell
            if t2 is not None and t1 is not None and t2 < t1:
                delay += t1 - t2
            i += 1
            j += 1
        return delay

    @property
    def all_layers_single_product(self) -> bool:
        return all(isinstance(layer_request, LayerRequestSingleProduct) for layer_request in self.sub_jobs)

    @property
    def all_layers_multi_product(self) -> bool:
        return all(isinstance(layer_request, LayerRequirementMultiProduct) for layer_request in self.sub_jobs)

    @property
    def is_top_off(self) -> bool:
        return not self.all_layers_single_product and not self.all_layers_multi_product
