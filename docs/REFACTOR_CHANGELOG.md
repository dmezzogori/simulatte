# Simulatte Refactor Changelog

## Overview

This document tracks the progress of the job-shop core refactor.

**Goal:** Make job-shop the core DES library with unified Server-based intralogistics.

**Baseline:** 148 tests passing, 5 failing (job-shop tests due to singleton/env issues)

---

## Phase 0: Baseline & Safety (Completed)

- Ran baseline tests: 148 passed, 5 failed
- Created branch: `simulatte-refactor-jobshop-core`
- Tagged baseline: `pre-refactor-jobshop-core`
- Created this changelog

**Commit:** `chore: establish refactor baseline with pre-refactor tag`

---

## Phase 1+2: Unpackage Job-Shop and Delete Directory (Completed)

Combined into single commit for clean type checking.

**Files moved to root:**
- [x] `job.py`, `server.py`, `shopfloor.py`, `router.py`, `runner.py`
- [x] `builders.py`, `distributions.py`, `typing.py`
- [x] `faulty_server.py`, `inspection_server.py`
- [x] `policies/` (lumscor, slar, starvation_avoidance)
- [x] `psp.py` and `psp_policies/`

**Deleted:**
- [x] `src/simulatte/jobshop/` (entire directory)
- [x] `src/simulatte/jobs.py` (empty file)
- [x] `src/simulatte/utils/runner.py` (replaced by job-shop runner)
- [x] `tests/test_runner.py` (tested old runner)

**Tests:** Renamed `tests/jobshop` to `tests/core`

**Commit:** `refactor: unpackage jobshop modules to simulatte root and delete jobshop/`

---

## Phase 3: Explicit Environment Injection (Completed)

**Key changes:**
- [x] Removed `Singleton` metaclass from `ShopFloor`
- [x] `ShopFloor.servers` is now instance variable (not `ClassVar`)
- [x] Added `env: Environment` parameter to:
  - `Server`, `ShopFloor`, `Job`, `Router`, `PreShopPool`
- [x] Added `shopfloor` parameter to:
  - `Server`, `PreShopPool`, `LumsCor`
- [x] Updated `FaultyServer` to pass through `env`/`shopfloor`
- [x] Updated all builders to accept `env` and construct components explicitly
- [x] Updated `Runner` to pass `env` to builder (no more `Singleton.clear()`)
- [x] Replaced all `Environment().now` with `self._env.now` in `Job`
- [x] Updated all core tests for explicit injection

**Test results:** All 8 core tests pass

**Commit:** `refactor: implement explicit environment injection`

---

## Phase 4: Job Type Hierarchy (Completed)

**Key changes:**
- [x] Created `JobType` enum (PRODUCTION, TRANSPORT, WAREHOUSE)
- [x] Created `BaseJob` abstract base class with shared functionality
- [x] Created `ProductionJob` with `material_requirements: dict[int, dict[str, int]]`
- [x] Created `TransportJob` with origin/destination/cargo
- [x] Created `WarehouseJob` with product/quantity/operation_type
- [x] Added `Job = ProductionJob` alias for backward compatibility
- [x] Updated Server to use `BaseJob` instead of `Job` for type hints
- [x] Added comprehensive tests in `tests/core/test_job.py`

**Test results:** 18 core tests pass

**Commit:** `refactor: implement job type hierarchy`

---

## Phase 5: WarehouseStore & AGVServer (Completed)

**WarehouseStore (`warehouse_store.py`):**
- [x] Extends `Server` with `n_bays` capacity
- [x] Per-product `simpy.Container` inventory
- [x] `pick_inventory()` method - blocks until inventory available
- [x] `put_inventory()` method
- [x] Metrics: `total_picks`, `total_puts`, `average_pick_time`, `average_put_time`

**AGVServer (`agv_server.py`):**
- [x] Extends `Server` with `capacity=1`
- [x] Configurable `travel_time_fn(origin, destination)`
- [x] `travel()` and `travel_to()` methods
- [x] Metrics: `trip_count`, `total_travel_time`, `average_travel_time`

**Test results:** 31 core tests pass

**Commit:** `feat: implement WarehouseStore and AGVServer`

---

## Phase 6: MaterialCoordinator (Completed)

**MaterialCoordinator (`materials.py`):**
- [x] `ensure(job, server, op_index)` - blocks while holding server (FIFO blocking)
- [x] Coordinates warehouse pick and AGV transport
- [x] Creates `WarehouseJob` and `TransportJob` for tracking
- [x] Metrics: `total_deliveries`, `average_delivery_time`

**Also updated:**
- [x] `Server`, `FaultyServer`, `InspectionServer` to use `BaseJob` instead of `Job`

**Test results:** 38 core tests pass

**Commit:** `feat: implement MaterialCoordinator with FIFO blocking`

---

## Phase 7: Builders, Tests, Docs (Completed)

**MaterialSystemBuilder (`builders.py`):**
- [x] `build(env, ...)` static method
- [x] Configurable: `n_servers`, `n_agvs`, `n_bays`
- [x] Configurable: `products`, `initial_inventory`
- [x] Configurable: `pick_time`, `put_time`, `travel_time`
- [x] Returns: `(shopfloor, servers, warehouse, agvs, coordinator)`

**Integration tests (`tests/core/test_integration.py`):**
- [x] Builder configuration tests
- [x] Full simulation with and without materials
- [x] FIFO blocking verification
- [x] Warehouse inventory blocking
- [x] AGV metrics accumulation

**Test results:** 45 core tests pass

**Commit:** `feat: add MaterialSystemBuilder and integration tests`

---

## Phase 8: Delete Unused Modules (Completed)

**Deleted directories:**
- [x] `src/simulatte/agv/` - Replaced by `AGVServer`
- [x] `src/simulatte/stores/` - Replaced by `WarehouseStore`
- [x] `src/simulatte/picking_cell/` - No longer needed
- [x] `src/simulatte/controllers/` - Replaced by `MaterialCoordinator`
- [x] `src/simulatte/operations/` - Replaced by Job types
- [x] `src/simulatte/protocols/` - Referenced deleted modules
- [x] `src/simulatte/distance/` - Referenced deleted modules
- [x] `src/simulatte/events/` - Referenced deleted modules
- [x] `src/simulatte/unitload/` - Referenced deleted modules
- [x] `src/simulatte/demand/` - Referenced deleted modules
- [x] `src/simulatte/buffer/` - Referenced deleted modules
- [x] `src/simulatte/observables/` - Referenced deleted modules
- [x] `src/simulatte/resources/` - Referenced deleted modules
- [x] `src/simulatte/service_point/` - Referenced deleted modules
- [x] `src/simulatte/simpy_extension/` - Referenced deleted modules
- [x] `src/simulatte/typings/` - Referenced deleted modules
- [x] `src/simulatte/exceptions/` - Referenced deleted modules

**Deleted files:**
- [x] `src/simulatte/requests.py`, `robot.py`, `products.py`, `simulation.py`, `reporting.py`
- [x] `src/simulatte/policies/retrieval_policy.py`, `storing_policy.py`, `agv_selection_policy/`

**Deleted tests:**
- [x] All tests referencing deleted modules

**Kept:**
- [x] `utils/` (singleton, identifiable_mixin, as_process, env_mixin, priority)
- [x] `environment.py`, `location.py`, `logger.py`
- [x] Core job-shop modules and new material handling modules

**Test results:** 96 tests pass

**Commit:** `refactor: delete legacy modules replaced by new core`

---

## Summary

The refactor is complete. Simulatte now has:

1. **Core job-shop DES library** with explicit environment injection
2. **Job type hierarchy** (ProductionJob, TransportJob, WarehouseJob)
3. **Unified Server-based intralogistics**:
   - `WarehouseStore` - inventory management with blocking picks
   - `AGVServer` - transport with travel time tracking
   - `MaterialCoordinator` - orchestrates delivery with FIFO blocking
4. **MaterialSystemBuilder** for easy system configuration
5. **96 passing tests** covering all functionality

**Key improvements:**
- No more singleton pattern - fully explicit environment injection
- Clean separation between job types
- Unified Server interface for all resources
- Comprehensive metrics tracking
- FIFO blocking semantics for material delivery
