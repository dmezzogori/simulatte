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

## Phase 4: Job Type Hierarchy

**Status:** Not started

**Planned:**
- Create `JobType` enum (PRODUCTION, TRANSPORT, WAREHOUSE)
- Create `Job` base class (abstract)
- Create `ProductionJob` with `material_requirements`
- Create `TransportJob` with origin/destination/cargo
- Create `WarehouseJob` with product/quantity/operation_type
- Keep `Job = ProductionJob` for backward compatibility

---

## Phase 5: WarehouseStore & AGVServer

**Status:** Not started

---

## Phase 6: MaterialCoordinator

**Status:** Not started

---

## Phase 7: Builders, Tests, Docs

**Status:** Not started

---

## Phase 8: Delete Unused Modules

**Status:** Not started
