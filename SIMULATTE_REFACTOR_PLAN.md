# Simulatte Refactor Plan (Job-Shop Core + Intralogistics)

Date: 2025-12-23  
Status: Approved direction, implementation not started yet (this file is the pre-flight plan)

## 1) Aim (what we are building)

Refactor Simulatte into a single, cohesive **discrete-event simulation core** where:

- The **job-shop** model becomes the **core library** (moved from `simulatte.jobshop` to `src/simulatte/`).
- **Intralogistics** (Warehouse + AGVs) is modeled **in job-shop terms**:
  - `WarehouseStore` **extends** the same `Server` base used by machines.
  - Each AGV is a `Server` instance (`AGVServer`, capacity=1).
  - A unified KPI surface exists for **Machine / AGV / Warehouse** via the `Server` base.
- Materials are coordinated via an explicit **material flow** process:
  - Inventory can be insufficient.
  - Materials may be required at **any server** in the routing (not only first operation).
  - Materials are optional per operation (some ops require none).
  - The system uses a simplified distance model (Warehouse ↔ Server), suitable for scale.

This refactor intentionally drops project-specific implementations and backward compatibility.

## 2) Scope (what is in / out)

### In scope

- Unpackage and make job-shop modules the Simulatte root DES core (`src/simulatte/`).
- Enforce **explicit environment injection** everywhere (no singleton environment; one clock per simulation).
- Implement a **job-centric** `Server` model:
  - `Server.request(job=...)` remains the primary interface.
  - All capacity contention and processing occurs through `Job` instances.
  - AGV and warehouse operations are represented as jobs (specialized job types).
- Implement simplified intralogistics:
  - `WarehouseStore(Server)` with bay capacity + inventory + service times.
  - `AGVServer(Server)` with travel time function and minimal trip metrics.
- Implement a material coordinator with strict FIFO semantics (see §4.6).
- Delete modules that are no longer needed after the new core is in place.
- Update tests and docs to reflect the new architecture.

### Out of scope (explicitly)

- Backward compatibility (no shims, no legacy exports).
- Keeping existing detailed models (current `agv/`, `stores/`, `picking_cell/`, and controllers may be removed).
- Per-location `Server` instances for warehouse slots (too heavy at the required scale).
- RL / Gym / training components from `rl-ppc` (not needed).

## 3) Clarifications & decisions (source of truth)

These decisions are explicitly confirmed and must be preserved during implementation:

1. **Target scenario**
   - Manufacturing job-shop + intralogistics.
   - Reframe warehouse/AGV/picking-cell concepts into job-shop elements, i.e. `Server`.

2. **Warehouse model**
   - Simpler abstraction is OK.
   - `WarehouseStore` must extend the unified `Server` class.

3. **Environment**
   - `simulatte.jobshop` becomes **fully explicit env**.
   - After refactor, the whole library is explicit env injection (single clock per simulation instance).

4. **Packaging**
   - `src/simulatte/jobshop` must be **deleted**.
   - Job-shop code is moved directly under `src/simulatte/`.
   - If there are naming conflicts, job-shop-derived components **take priority** and legacy components may be deleted.

5. **Unified KPI**
   - Mandatory across machine/AGV/warehouse.
   - Simplification is acceptable to achieve KPI unification.

6. **Materials**
   - Inventory can be insufficient.
   - Missing materials can stall jobs.
   - Materials can be requested at any server (any routing step).
   - Materials may be “general consumables” or job-specific, but initial modeling can start with `product + quantity`.
   - Materials are optional: some jobs/operations require no materials.

7. **Routing & blocking semantics**
   - Preserve strict FIFO on server queues, even under material starvation.
   - Head-of-line jobs can block others (no bypass/resequencing).

8. **Distance model**
   - OK to model travel distance/time as Warehouse ↔ Server.

9. **Expected scale**
   - 500–1000 warehouse locations (but *not* modeled as Servers).
   - 20–100 AGVs.
   - Thousands of jobs per run.

## 4) Target design (high-level)

### 4.1 Module layout (after unpackaging)

After refactor, `src/simulatte/` becomes the single canonical DES API.

Proposed modules (names confirmed):

- `src/simulatte/environment.py` — `Environment` (explicit)
- `src/simulatte/job.py` — `Job` base + specialized job types (see below)
- `src/simulatte/server.py` — `Server` base (job-centric) + `ServerPriorityRequest`
- `src/simulatte/shopfloor.py` — `ShopFloor` orchestration (explicit instance, not singleton)
- `src/simulatte/router.py` — `Router` (explicit env; generates jobs)
- `src/simulatte/psp.py` — `PreShopPool`
- `src/simulatte/policies/` — release/priority policies (LUMS-COR, SLAR, DRACO, starvation avoidance)
- `src/simulatte/distributions.py` — distributions used by builders
- `src/simulatte/builders.py` — builders for common systems (job-shop only; job-shop + materials)
- `src/simulatte/runner.py` — runner for repeated simulations (explicit env, parallel-safe)
- `src/simulatte/materials.py` — material coordination logic (Warehouse, AGVs, strict FIFO stalling)

The old `src/simulatte/jobshop/` directory is removed.

### 4.2 Core contract: explicit environment

All stateful DES objects receive `env: Environment` explicitly:

- `Server(env=...)`
- `ShopFloor(env=...)`
- `Router(env=...)`
- `PreShopPool(env=...)`
- `Runner` constructs `env` per run and passes it through builder.

There must be no implicit `Environment()` calls inside components.

### 4.3 Unified Server base (job-centric)

Constraint: `Server` is **not** job-agnostic. Every interaction must be via `Job`.

Implications:

- `Server.request(job=..., preempt=...)` remains the API.
- Priority calculations remain job-driven (`job.priority(server)`).
- “AGV missions” and “warehouse operations” are modeled as `Job` subtypes that still satisfy `Job.priority(server)`.

Unified KPIs supported on `Server`:

- `worked_time`
- `utilization_rate`
- `average_queue_length`
- Optional: queue/utilization time series (must be controllable to avoid memory blowup at scale).

### 4.4 Job model (production + logistics)

We keep a single `Job` base and add specialized variants as needed:

- `ProductionJob`: classic job-shop job with routing and processing times.
- `TransportJob`: “move materials from Warehouse to Server”.
- `WarehouseJob`: “pick/store material at Warehouse”.

These can either be separate classes or a single class with typed fields; the key is that all are “Jobs” and can be processed by `Server`.

### 4.5 WarehouseStore(Server) (simplified)

Warehouse responsibilities are reduced to:

- **Capacity contention**: bays modeled via `Server(capacity=n_bays)`.
- **Inventory**: per-product inventory modeled via `simpy.Container` or equivalent:
  - `Container.get(qty)` blocks when insufficient (models starvation).
  - optional “supply process” can add inventory over time.
- **Service times**: `pick_time`, `put_time` as distributions/callables.

We explicitly do **not** model 500–1000 locations as `Server`s. Locations can remain lightweight data or be summarized as capacity/occupancy counters.

### 4.6 AGVServer(Server) (simplified)

AGV model:

- Each AGV is `AGVServer(Server, capacity=1)`.
- Travel time computed by `travel_time_fn(warehouse, destination_server)` (Warehouse ↔ Server only).
- Minimal KPIs:
  - As a `Server`, utilization/queue metrics exist.
  - Additional counters: total travel time, trip count (optional).

### 4.7 Materials + strict FIFO blocking

Requirement: strict FIFO even when materials are missing (head-of-line can block).

Proposed mechanism:

- Each `ProductionJob` has **per-operation** optional material requirements, e.g.:
  - `job.material_requirements[op_index] = {product: qty, ...}` or empty/None.
- In `ShopFloor.main(job)` (or inside `Server.process_job`), for each operation:
  1. Request server (FIFO/priority queue) with `with server.request(job=job) as req: yield req`.
  2. **If materials required**, block while holding the server:
     - `yield env.process(materials.ensure(job, server, op_index))`
  3. Perform processing time: `yield env.timeout(processing_time)`.
  4. Release happens normally (context manager exit / explicit release).

This satisfies “strict FIFO and potentially block others”: the server is acquired by the job; if materials are missing, the server is effectively held idle while waiting for material delivery.

MaterialCoordinator responsibilities:

- Reads material requirements for a (job, server, op_index).
- Waits for warehouse inventory (`Container.get`), blocks when insufficient.
- Requests `WarehouseStore` bay capacity to perform pick.
- Requests an `AGVServer` to transport Warehouse → server (travel time).
- Signals completion back to the waiting job.

Materials are optional: if requirement is empty/None, `ensure` returns immediately.

## 5) Performance/memory guardrails (“don’t over-engineer”)

Given scale (thousands of jobs, up to 100 AGVs):

- Avoid `Server` per warehouse slot; model inventory in aggregate.
- Default metrics should be lightweight:
  - Keep `worked_time` and queue-time histogram for average queue length.
  - Make any full time-series collection opt-in (or at least bounded) to avoid memory blow-ups.
- MaterialCoordinator should be event-driven (e.g., per-operation `ensure()` triggers work), not global polling over all jobs.

## 6) Step-by-step execution instructions (how to proceed)

### Step 0 — Baseline & safety checks

1. Run tests to capture current baseline:
   - `pytest -q`
2. Record that current jobshop tests fail due to env mismatch (different clocks).
3. Ensure git status clean before starting (recommended):
   - `git status`
4. Create a new branch for the refactor:
   - `git checkout -b simulatte-refactor-jobshop-core`
5. Optionally, tag the current commit for easy reference:
   - `git tag pre-refactor-jobshop-core`
6. Prepare to commit frequently during the refactor.
7. Before each major step, run tests to ensure no regressions.
8. Before proceeding to the next step, create a markdown file documenting the changes made, to be updated at each step.
9. Before proceeding to the next step, commit all changes from the previous step.

### Step 1 — Unpackage job-shop into root

1. Move jobshop modules into root using `git mv`, mapping to the module layout in §4.1.
2. Update imports inside moved files to point to `simulatte.*` root modules.
3. Resolve naming collisions by deleting/overwriting legacy modules as needed (jobshop wins).

### Step 2 — Delete `src/simulatte/jobshop/`

1. Delete the directory after all moved files are in place and imports updated.
2. Update any remaining references in tests/docs.

### Step 3 — Make env explicit everywhere

1. Update constructors to accept `env` and store `self.env = env`.
2. Remove:
   - `Singleton` metaclass usage for `ShopFloor`.
   - Any internal `Environment()` construction in `Server/ShopFloor/Router/PSP/builders/Runner`.
3. Update builders to construct one env and pass it to every component.
4. Update tests to create `env = Environment()` and pass it through.

### Step 4 — Enforce job-centric Server semantics + unified KPIs

1. Keep `ServerPriorityRequest(job=...)` as the request type.
2. Ensure that AGV/Warehouse operations are implemented as `Job` subclasses and run through the same request/process path.
3. Keep KPI computations consistent across all server types.

### Step 5 — Add WarehouseStore(Server) + AGVServer(Server)

1. Implement `WarehouseStore` with:
   - bay capacity (`capacity=n_bays`)
   - inventory (`Container` per product)
   - pick/put service times
2. Implement `AGVServer` with:
   - travel time function (Warehouse ↔ Server)
   - minimal trip counters

### Step 6 — Implement materials coordinator + strict FIFO stalling

1. Extend `ProductionJob` to include per-operation material requirements (optional).
2. Implement `MaterialCoordinator.ensure(job, server, op_index)` as described in §4.7.
3. Integrate `ensure()` into shopfloor execution while holding the server resource.
4. Add a simple supply process (optional) to allow inventory to replenish over time.

### Step 7 — Update builders, policies, tests, docs

1. Builders:
   - jobshop-only (no materials)
   - jobshop + materials (warehouse + AGVs + coordinator)
2. Policies:
   - port existing jobshop release/priority policies into root `policies/`
3. Tests:
   - Update existing jobshop tests to new module paths.
   - Add new tests:
     - materials missing stalls job while holding server
     - materials at later routing steps
     - unified KPI available for machine/warehouse/agv
4. Docs:
   - Update `docs/simulatte-architecture.md` and refactor docs to match actual architecture.

### Step 8 — Delete unused modules (no legacy)

1. Identify modules unused by the new core.
2. Delete:
   - old intralogistics implementations (`agv/`, `stores/`, `picking_cell/`, controllers, etc.) if not referenced.
3. Remove corresponding tests if they test deleted functionality.
4. Re-run test suite.

## 7) Remaining open items (to decide during implementation)

- The exact representation of “general consumables” vs job-specific materials:
  - Start with per-operation `{product: qty}`; later can add flags like `consumable=True`.
- Partial deliveries (split qty across multiple AGVs) are not required initially.
- Priority rules for material transport jobs (initially: FIFO, or derived from blocked production job priority).

