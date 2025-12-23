# Simulatte Refactor Changelog

## Overview

This document tracks the progress of the job-shop core refactor.

**Goal:** Make job-shop the core DES library with unified Server-based intralogistics.

**Baseline:** 148 tests passing, 5 failing (jobshop tests due to singleton/env issues)

---

## Phase 0: Baseline & Safety (Completed)

- Ran baseline tests: 148 passed, 5 failed
- Created branch: `simulatte-refactor-jobshop-core`
- Tagged baseline: `pre-refactor-jobshop-core`
- Created this changelog

---

## Phase 1: Unpackage Job-Shop into Root

**Status:** Not started

**Files to move:**
- [ ] `jobshop/job.py` → `job.py`
- [ ] `jobshop/shopfloor.py` → `shopfloor.py`
- [ ] `jobshop/router.py` → `router.py`
- [ ] `jobshop/runner.py` → `runner.py`
- [ ] `jobshop/distributions.py` → `distributions.py`
- [ ] `jobshop/typing.py` → `typing.py`
- [ ] `jobshop/builders.py` → `builders.py`
- [ ] `jobshop/server/*` → root level
- [ ] `jobshop/psp/*` → root level
- [ ] `jobshop/policies/*` → `policies/`

---

## Phase 2: Delete jobshop/ Directory

**Status:** Not started

---

## Phase 3: Explicit Environment Injection

**Status:** Not started

---

## Phase 4: Job Type Hierarchy

**Status:** Not started

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
