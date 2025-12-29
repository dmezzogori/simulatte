"""MaterialCoordinator - Orchestrates material delivery with FIFO blocking."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simulatte.environment import Environment
from simulatte.job import ProductionJob, TransportJob, WarehouseJob

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.agv import AGV
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor
    from simulatte.typing import ProcessGenerator
    from simulatte.warehouse_store import WarehouseStore


class MaterialCoordinator:
    """Orchestrates material delivery from warehouse to production servers.

    The MaterialCoordinator implements strict FIFO blocking semantics: when a
    ProductionJob requires materials at an operation, it holds the server
    (blocks the queue) while waiting for materials to be picked and delivered.
    This ensures jobs are processed in arrival order even when material
    delivery times vary.

    The delivery process for each material requirement:
    1. Request a warehouse bay (wait for availability)
    2. Pick the product from inventory (blocks if insufficient stock)
    3. Request an AGV (wait for availability)
    4. Transport materials to the destination server

    Attributes:
        env: The simulation environment.
        warehouse: The WarehouseStore to pick from.
        agvs: List of available AGVs for transport.
        shopfloor: The ShopFloor for job tracking.
    """

    def __init__(
        self,
        *,
        env: Environment,
        warehouse: WarehouseStore,
        agvs: list[AGV],
        shopfloor: ShopFloor,
    ) -> None:
        """Initialize a MaterialCoordinator.

        Args:
            env: The simulation environment.
            warehouse: The warehouse to pick materials from.
            agvs: List of AGV servers for transport.
            shopfloor: The shopfloor for job tracking.
        """
        self.env = env
        self.warehouse = warehouse
        self.agvs = agvs
        self.shopfloor = shopfloor
        self._agv_rr_cursor: int = 0

        # Metrics
        self.total_deliveries: int = 0
        self.total_delivery_time: float = 0

    def ensure(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
    ) -> ProcessGenerator:
        """Ensure materials are delivered before processing can begin.

        This method blocks (while holding the server) until all required
        materials for the operation are picked from the warehouse and
        delivered to the server. This implements FIFO blocking - the job
        holds its position in the queue while waiting for materials.

        Args:
            job: The production job requiring materials.
            server: The server where processing will occur.
            op_index: The operation index (0-based).

        Yields:
            SimPy events for the complete delivery process.
        """
        requirements = job.get_materials_for_operation(op_index)
        if not requirements:
            return  # No materials needed for this operation

        self.env.debug(
            f"Material delivery triggered for job {job.id[:8]}",
            component="MaterialCoordinator",
            job_id=job.id,
            server_id=server._idx,
            op_index=op_index,
            materials=requirements,
        )

        start_time = self.env.now

        for product, quantity in requirements.items():
            yield from self._deliver_material(product, quantity, server, job)

        delivery_time = self.env.now - start_time
        self.total_deliveries += 1
        self.total_delivery_time += delivery_time

        self.env.debug(
            f"Material delivery completed for job {job.id[:8]}",
            component="MaterialCoordinator",
            job_id=job.id,
            server_id=server._idx,
            delivery_time=delivery_time,
            total_deliveries=self.total_deliveries,
        )

    def _deliver_material(
        self,
        product: str,
        quantity: int,
        destination: Server,
        parent_job: ProductionJob,
    ) -> ProcessGenerator:
        """Deliver a single material from warehouse to destination.

        Args:
            product: Product name to deliver.
            quantity: Quantity to deliver.
            destination: Server to deliver to.
            parent_job: The production job this delivery supports.

        Yields:
            SimPy events for pick and transport.
        """
        # Create a warehouse job for the pick operation
        pick_job = WarehouseJob(
            env=self.env,
            warehouse=self.warehouse,
            product=product,
            quantity=quantity,
            operation_type="pick",
            processing_time=0,  # Time is handled by pick_inventory
        )

        self.env.debug(
            f"Warehouse pick requested: {quantity}x {product}",
            component="MaterialCoordinator",
            product=product,
            quantity=quantity,
            warehouse_id=self.warehouse._idx,
        )

        # Request a warehouse bay and perform pick
        with self.warehouse.request(job=pick_job) as warehouse_request:
            yield warehouse_request
            yield self.env.process(self.warehouse.pick_inventory(product, quantity))

        # Select an AGV (load-balanced by current workload)
        agv = self._select_agv(destination, parent_job)

        # Create transport job
        transport_job = TransportJob(
            env=self.env,
            origin=self.warehouse,
            destination=destination,
            cargo={product: quantity},
        )

        self.env.debug(
            f"AGV transport started: {quantity}x {product}",
            component="MaterialCoordinator",
            agv_id=agv.agv_id,
            product=product,
            quantity=quantity,
            destination_id=destination._idx,
        )

        # Transport materials
        with agv.request(job=transport_job) as agv_request:
            yield agv_request
            yield self.env.process(agv.travel(self.warehouse, destination))

    def _select_agv(
        self,
        destination: Server,  # noqa: ARG002
        parent_job: ProductionJob,  # noqa: ARG002
    ) -> AGV:
        """Select an AGV using a lightweight load-balancing heuristic.

        The default heuristic chooses the AGV with the smallest current workload,
        defined as `agv.count + len(agv.queue)` (busy + waiting). Ties are broken
        round-robin to distribute work across identical candidates.

        Args:
            destination: Where the AGV needs to go (for smart selection).
            parent_job: The production job this supports (for priority).

        Returns:
            The selected AGV.
        """
        if not self.agvs:
            raise ValueError("MaterialCoordinator has no AGVs configured.")

        best_load = min(agv.count + len(agv.queue) for agv in self.agvs)
        start = self._agv_rr_cursor % len(self.agvs)

        for offset in range(len(self.agvs)):
            idx = (start + offset) % len(self.agvs)
            agv = self.agvs[idx]
            if agv.count + len(agv.queue) == best_load:
                self._agv_rr_cursor = (idx + 1) % len(self.agvs)

                self.env.debug(
                    f"AGV selected: {agv.agv_id}",
                    component="MaterialCoordinator",
                    agv_id=agv.agv_id,
                    workload=best_load,
                    agv_count=len(self.agvs),
                )

                return agv

        # Fallback (should be unreachable given best_load computation).
        return self.agvs[start]  # pragma: no cover

    @property
    def average_delivery_time(self) -> float:
        """Average time to complete a material delivery."""
        if self.total_deliveries == 0:
            return 0.0
        return self.total_delivery_time / self.total_deliveries
