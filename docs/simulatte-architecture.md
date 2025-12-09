# Simulatte Architecture (Core Library, excluding `jobshop`)

Analysis date: 2025-12-09  
Scope: `src/simulatte` top-level package, excluding `jobshop`

## Overview
- Discrete-event simulation framework built on SimPy.
- Explicit environment injection (no global singleton); thin `Environment` wrapper and abstract `Simulation` base provide lifecycle, seeding, and run/summary hooks.
- Domain focus: warehouse intralogistics—AGVs move unit loads between stores and robotic picking cells; demand arrives as pallet orders composed of order lines.
- Control layer uses lightweight controllers with pluggable strategy callables instead of heavyweight policy classes.
- Infrastructure utilities supply reusable mixins, monitored resources, and simplified queues/stores to keep models concise.

## Core Runtime & Utilities
- `Environment` (`environment.py`): thin `simpy.Environment` subclass; previously singleton, now explicitly instantiated.
- `Simulation` (`simulation.py`): abstract template with `build`, `run`, `results`, `summary`; resets IDs, seeds RNGs, and wires an environment.
- Mixins: `EnvMixin` enforces explicit `env`; `_require_env` throws if omitted. `IdentifiableMixin` stamps unique IDs per class. `TimedMixin` (in `requests.py`) captures start/end times.
- Process helper: `as_process` decorator registers generator functions as SimPy processes using the provided env.
- Runner: `Runner` executes multiple simulation replicas sequentially or via multiprocessing with per-run seeds.
- Logging: Loguru-based logger patched to annotate messages with simulation time.
- Store/queue primitives (`simpy_extension`): FIFO `SequentialStore`, multi-get `MultiStore`/`FilterMultiStore`, key/value `HashStore`, and a simple priority queue helper.
- Resource monitoring: `MonitoredResource` and `MonitoredRequest` track worked time, idle time, saturation history.

## Domain Components
### AGV System (`agv/`)
- `AGV` extends `PriorityResource`, tracks missions (`AGVMission`) and trips (`AGVTrip`), enforces load/unload timeouts, keeps travel/wait/idle/saturation metrics, and offers plotting (`AGVPlotter`).
- Enumerations: `AGVKind` (FEEDING/REPLENISHMENT/INPUT/OUTPUT) and `AGVStatus` (IDLE, traveling, waiting to load/unload).
- `AGVTrip`/`AGVMission` capture per-trip and per-mission timing; distance/status specialization left to concrete subclasses of `AGVTrip`.

### Warehouse Stores (`stores/`)
- `WarehouseStore`: dual-sided, multi-floor storage; manages input/output locations, conveyors (abstracted), saturation counters, AGV queues, and unit-load history.
- `WarehouseLocation`: depth-2 slot with physical positions, product compatibility checks, booking/freezing of future loads, and pickup booking; distance helpers (`distance.py`) support placement policies.
- `Traslo`: stacker-crane-style mover that tracks handling time and saturation.
- Inventory helpers in `inventory_position.py`; generic store operations and load/unload orchestration in `operation.py`.

### Picking Cells (`picking_cell/`)
- `PickingCell` owns input/output queues, a robot, and three logical areas: `FeedingArea`, `StagingArea`, `InternalArea` (all based on `Area` with history tracking).
- Flow helpers `_pump_feeding_pipeline`, `_shift_feeding_to_staging`, `_shift_staging_to_internal` keep operations moving without an observer layer.
- `Position` resources guard unload/pre-unload slots; `InternalArea` optionally manages paired pre-unload/unload positions.
- Productivity/starvation hooks exposed for specialization; summary plots robot and area queues.
- `Robot` simulates pick/place/rotate with saturation and productivity history.

### Feeding Operations (`operations/feeding_operation.py`)
- `FeedingOperation` ties a pallet order line to an AGV and a store; logs every step (retrieval, trips, loading, staging/internal moves, return/drop) via `FeedingOperationLog`.
- Signals readiness with `LoggedEvent`; exposes chain relationships between operations serving the same pallet request.

### Demand & Requests
- Protocol-based job generator (`demand/jobs_generator.py`) yields shifts → customer orders → pallet orders.
- `OrderLine` and `PalletOrder` (alias `PalletRequest`) flatten request hierarchy; track lead times and aggregate feeding operations.

### Unit Loads & Products
- Products (`products.py`) carry probabilities, family, cases/layer, layers/pallet, min/max cases; `ProductsGenerator` builds catalogs from distributions.
- Unit-load model (`unitload/`): pallets composed of layers; supports single- and multi-product pallets and layers; counts cases per product, exposes upper-layer access.

## Control & Policy Surface
- `SystemController`: wires AGV, cell, and store controllers; maintains PSP (pre-shop pool) of pallet requests; abstract hooks to release pallet requests and manage feeding/retrieval—meant to be subclassed per scenario.
- `AGVController`: partitions fleet by role and selects candidates via injected callables (`least_busy_agv`, `idle_feeding_order`).
- `CellsController`: registry of picking cells with selectable strategy (`first_available` default).
- `StoresController`: abstract manager that applies injected retrieval/storing policies, books locations, and maintains stock/on-transit counts; requires `RetrievalPolicy` and `StoringPolicy` protocols (`policies/`).
- Distance abstraction: `DistanceController` and base `Distance` allow pluggable distance metrics.

## Design Patterns & Architectural Intent
- Strategy: selection callables for AGVs/cells; retrieval/storing policies; distance calculators.
- Template/Abstract Base: `Simulation`, `SystemController`, `StoresController`, `Distance`, `AGVTrip`, `PickingCell.process_job` define extension points for domain-specific behavior.
- Mixins: `EnvMixin`, `IdentifiableMixin`, `TimedMixin` provide cross-cutting concerns.
- Context Manager: `AGV.trip` wraps move bookkeeping; `MonitoredRequest` wraps resource usage.
- Factory-style generation: `ProductsGenerator`, `PalletSingleProduct.by_product`.
- Observability: Areas log queue levels; plotters on AGV/Robot/Store expose diagnostics; logger timestamps with sim time.

## High-Level Capabilities
- End-to-end modeling of pallet requests from demand generation through store retrieval, AGV transport, robotic picking, and output handoff.
- Captures KPIs: AGV travel/wait/idle, robot saturation/productivity, store queue lengths and saturation, picking-cell productivity/workload.
- Emphasizes explicit wiring and composability: no global env, protocol-defined policies, small helper primitives to assemble bespoke warehouse simulations (AGV feeding + robotic picking), with domain specializations left to user extensions.
