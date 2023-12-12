from __future__ import annotations

from typing import Protocol

from simulatte.operations.feeding_operation import FeedingOperation
from simulatte.protocols.has_product import HasManyProduct, HasSingleProduct
from simulatte.protocols.hold_cases import HoldCases
from simulatte.protocols.identifiable import Identifiable
from simulatte.protocols.job import Job
from simulatte.unitload import Pallet


class PickingRequest(Job, Identifiable, HoldCases, Protocol):
    feeding_operations: list[FeedingOperation]


class CaseRequest(PickingRequest, HasSingleProduct, Protocol):
    parent: ProductRequest
    sub_jobs: None


class ProductRequest(PickingRequest, HasSingleProduct, Protocol):
    parent: LayerRequest
    sub_jobs: tuple[CaseRequest, ...]


class LayerRequest(PickingRequest, Protocol):
    parent: PalletRequest
    sub_jobs: tuple[ProductRequest, ...]


class PalletRequest(PickingRequest, HasManyProduct, Protocol):
    parent: None
    sub_jobs: tuple[LayerRequest, ...]
    unit_load: Pallet
