# Simulatte Refactor Plan (Job-Shop Core + Intralogistics)

<metadata>
  <date>2025-12-23</date>
  <status>Approved direction, implementation not started yet (this file is the pre-flight plan)</status>
</metadata>

## 1) Aim

<aim>
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
</aim>

## 2) Scope

<scope>
  <in-scope>
    <item>Unpackage and make job-shop modules the Simulatte root DES core (`src/simulatte/`).</item>
    <item>Enforce **explicit environment injection** everywhere (no singleton environment; one clock per simulation).</item>
    <item>
      Implement a **job-centric** `Server` model:
      <detail>`Server.request(job=...)` remains the primary interface.</detail>
      <detail>All capacity contention and processing occurs through `Job` instances.</detail>
      <detail>AGV and warehouse operations are represented as jobs (specialized job types).</detail>
    </item>
    <item>
      Implement simplified intralogistics:
      <detail>`WarehouseStore(Server)` with bay capacity + inventory + service times.</detail>
      <detail>`AGVServer(Server)` with travel time function and minimal trip metrics.</detail>
    </item>
    <item>Implement a material coordinator with strict FIFO semantics (see §4.6).</item>
    <item>Delete modules that are no longer needed after the new core is in place.</item>
    <item>Update tests and docs to reflect the new architecture.</item>
  </in-scope>

  <out-of-scope>
    <item reason="intentional-break">Backward compatibility (no shims, no legacy exports).</item>
    <item reason="replaced">Keeping existing detailed models (current `agv/`, `stores/`, `picking_cell/`, and controllers may be removed).</item>
    <item reason="scale">Per-location `Server` instances for warehouse slots (too heavy at the required scale).</item>
    <item reason="not-needed">RL / Gym / training components from `rl-ppc` (not needed).</item>
  </out-of-scope>
</scope>

## 3) Clarifications & Decisions (Source of Truth)

<decisions>
These decisions are explicitly confirmed and must be preserved during implementation:

  <decision id="1" topic="target-scenario">
    <summary>Manufacturing job-shop + intralogistics.</summary>
    <detail>Reframe warehouse/AGV/picking-cell concepts into job-shop elements, i.e. `Server`.</detail>
  </decision>

  <decision id="2" topic="warehouse-model">
    <summary>Simpler abstraction is OK.</summary>
    <detail>`WarehouseStore` must extend the unified `Server` class.</detail>
  </decision>

  <decision id="3" topic="environment">
    <summary>`simulatte.jobshop` becomes **fully explicit env**.</summary>
    <detail>After refactor, the whole library is explicit env injection (single clock per simulation instance).</detail>
  </decision>

  <decision id="4" topic="packaging">
    <summary>`src/simulatte/jobshop` must be **deleted**.</summary>
    <detail>Job-shop code is moved directly under `src/simulatte/`.</detail>
    <detail>If there are naming conflicts, job-shop-derived components **take priority** and legacy components may be deleted.</detail>
  </decision>

  <decision id="5" topic="unified-kpi">
    <summary>Mandatory across machine/AGV/warehouse.</summary>
    <detail>Simplification is acceptable to achieve KPI unification.</detail>
  </decision>

  <decision id="6" topic="materials">
    <summary>Materials can cause stalls and are optional per operation.</summary>
    <detail>Inventory can be insufficient.</detail>
    <detail>Missing materials can stall jobs.</detail>
    <detail>Materials can be requested at any server (any routing step).</detail>
    <detail>Materials may be "general consumables" or job-specific, but initial modeling can start with `product + quantity`.</detail>
    <detail>Materials are optional: some jobs/operations require no materials.</detail>
  </decision>

  <decision id="7" topic="routing-blocking">
    <summary>Preserve strict FIFO on server queues, even under material starvation.</summary>
    <detail>Head-of-line jobs can block others (no bypass/resequencing).</detail>
  </decision>

  <decision id="8" topic="distance-model">
    <summary>OK to model travel distance/time as Warehouse ↔ Server.</summary>
  </decision>

  <decision id="9" topic="expected-scale">
    <summary>Scale targets for the simulation.</summary>
    <detail>500–1000 warehouse locations (but *not* modeled as Servers).</detail>
    <detail>20–100 AGVs.</detail>
    <detail>Thousands of jobs per run.</detail>
  </decision>
</decisions>

## 4) Target Design (High-Level)

### 4.1 Module Layout (After Unpackaging)

<module-layout>
After refactor, `src/simulatte/` becomes the single canonical DES API.

  <module path="src/simulatte/environment.py" description="Environment (explicit)" />
  <module path="src/simulatte/job.py" description="Job base + specialized job types" />
  <module path="src/simulatte/server.py" description="Server base (job-centric) + ServerPriorityRequest" />
  <module path="src/simulatte/shopfloor.py" description="ShopFloor orchestration (explicit instance, not singleton)" />
  <module path="src/simulatte/router.py" description="Router (explicit env; generates jobs)" />
  <module path="src/simulatte/psp.py" description="PreShopPool" />
  <module path="src/simulatte/policies/" description="release/priority policies (LUMS-COR, SLAR, DRACO, starvation avoidance)" />
  <module path="src/simulatte/distributions.py" description="distributions used by builders" />
  <module path="src/simulatte/builders.py" description="builders for common systems (job-shop only; job-shop + materials)" />
  <module path="src/simulatte/runner.py" description="runner for repeated simulations (explicit env, parallel-safe)" />
  <module path="src/simulatte/materials.py" description="material coordination logic (Warehouse, AGVs, strict FIFO stalling)" />

  <delete path="src/simulatte/jobshop/" reason="unpackaged into root" />
</module-layout>

### 4.2 Core Contract: Explicit Environment

<contract id="explicit-env">
All stateful DES objects receive `env: Environment` explicitly:

  <signature>Server(env=...)</signature>
  <signature>ShopFloor(env=...)</signature>
  <signature>Router(env=...)</signature>
  <signature>PreShopPool(env=...)</signature>
  <signature>Runner constructs env per run and passes it through builder.</signature>

  <constraint>There must be no implicit `Environment()` calls inside components.</constraint>
</contract>

### 4.3 Unified Server Base (Job-Centric)

<contract id="job-centric-server">
  <constraint>Server is **not** job-agnostic. Every interaction must be via `Job`.</constraint>

  <implication>`Server.request(job=..., preempt=...)` remains the API.</implication>
  <implication>Priority calculations remain job-driven (`job.priority(server)`).</implication>
  <implication>"AGV missions" and "warehouse operations" are modeled as `Job` subtypes that still satisfy `Job.priority(server)`.</implication>

  <kpis>
    <kpi name="worked_time" required="true" />
    <kpi name="utilization_rate" required="true" />
    <kpi name="average_queue_length" required="true" />
    <kpi name="time_series" required="false" note="must be controllable to avoid memory blowup at scale" />
  </kpis>
</contract>

### 4.4 Job Model (Production + Logistics)

<job-model>
We keep a single `Job` base and add specialized variants as needed:

  <job-type name="ProductionJob" description="classic job-shop job with routing and processing times" />
  <job-type name="TransportJob" description="move materials from Warehouse to Server" />
  <job-type name="WarehouseJob" description="pick/store material at Warehouse" />

  <note>These can either be separate classes or a single class with typed fields; the key is that all are "Jobs" and can be processed by `Server`.</note>
</job-model>

### 4.5 WarehouseStore(Server) (Simplified)

<component name="WarehouseStore" extends="Server">
  <responsibility name="capacity-contention">
    Bays modeled via `Server(capacity=n_bays)`.
  </responsibility>

  <responsibility name="inventory">
    Per-product inventory modeled via `simpy.Container` or equivalent:
    <detail>`Container.get(qty)` blocks when insufficient (models starvation).</detail>
    <detail>Optional "supply process" can add inventory over time.</detail>
  </responsibility>

  <responsibility name="service-times">
    `pick_time`, `put_time` as distributions/callables.
  </responsibility>

  <constraint>We explicitly do **not** model 500–1000 locations as `Server`s. Locations can remain lightweight data or be summarized as capacity/occupancy counters.</constraint>
</component>

### 4.6 AGVServer(Server) (Simplified)

<component name="AGVServer" extends="Server">
  <property name="capacity" value="1" />
  <property name="travel_time_fn" signature="travel_time_fn(warehouse, destination_server)" note="Warehouse ↔ Server only" />

  <kpis>
    <kpi name="utilization" source="Server base" />
    <kpi name="queue_metrics" source="Server base" />
    <kpi name="total_travel_time" optional="true" />
    <kpi name="trip_count" optional="true" />
  </kpis>
</component>

### 4.7 Materials + Strict FIFO Blocking

<component name="MaterialCoordinator">
  <requirement>Strict FIFO even when materials are missing (head-of-line can block).</requirement>

  <mechanism>
    <step order="1">Each `ProductionJob` has **per-operation** optional material requirements:
      `job.material_requirements[op_index] = {product: qty, ...}` or empty/None.
    </step>
    <step order="2">
      In `ShopFloor.main(job)` (or inside `Server.process_job`), for each operation:
      <substep order="2.1">Request server (FIFO/priority queue) with `with server.request(job=job) as req: yield req`.</substep>
      <substep order="2.2">**If materials required**, block while holding the server: `yield env.process(materials.ensure(job, server, op_index))`</substep>
      <substep order="2.3">Perform processing time: `yield env.timeout(processing_time)`.</substep>
      <substep order="2.4">Release happens normally (context manager exit / explicit release).</substep>
    </step>
  </mechanism>

  <note>This satisfies "strict FIFO and potentially block others": the server is acquired by the job; if materials are missing, the server is effectively held idle while waiting for material delivery.</note>

  <responsibilities>
    <item>Reads material requirements for a (job, server, op_index).</item>
    <item>Waits for warehouse inventory (`Container.get`), blocks when insufficient.</item>
    <item>Requests `WarehouseStore` bay capacity to perform pick.</item>
    <item>Requests an `AGVServer` to transport Warehouse → server (travel time).</item>
    <item>Signals completion back to the waiting job.</item>
  </responsibilities>

  <constraint>Materials are optional: if requirement is empty/None, `ensure` returns immediately.</constraint>
</component>

## 5) Performance/Memory Guardrails

<guardrails context="scale: thousands of jobs, up to 100 AGVs">
  <guardrail id="no-server-per-slot">
    Avoid `Server` per warehouse slot; model inventory in aggregate.
  </guardrail>

  <guardrail id="lightweight-metrics">
    Default metrics should be lightweight:
    <detail>Keep `worked_time` and queue-time histogram for average queue length.</detail>
    <detail>Make any full time-series collection opt-in (or at least bounded) to avoid memory blow-ups.</detail>
  </guardrail>

  <guardrail id="event-driven-coordinator">
    MaterialCoordinator should be event-driven (e.g., per-operation `ensure()` triggers work), not global polling over all jobs.
  </guardrail>
</guardrails>

## 6) Step-by-Step Execution Instructions

<execution-plan>

  <step id="0" name="Baseline & Safety Checks">
    <task>Run tests to capture current baseline: `pytest -q`</task>
    <task>Record that current jobshop tests fail due to env mismatch (different clocks).</task>
    <task>Ensure git status clean before starting: `git status`</task>
    <task>Create a new branch for the refactor: `git checkout -b simulatte-refactor-jobshop-core`</task>
    <task>Optionally, tag the current commit for easy reference: `git tag pre-refactor-jobshop-core`</task>
    <task>Prepare to commit frequently during the refactor.</task>
    <task>Before each major step, run tests to ensure no regressions.</task>
    <task>Before proceeding to the next step, create a markdown file documenting the changes made, to be updated at each step.</task>
    <task>Before proceeding to the next step, commit all changes from the previous step.</task>
  </step>

  <step id="1" name="Unpackage Job-Shop into Root">
    <task>Move jobshop modules into root using `git mv`, mapping to the module layout in §4.1.</task>
    <task>Update imports inside moved files to point to `simulatte.*` root modules.</task>
    <task>Resolve naming collisions by deleting/overwriting legacy modules as needed (jobshop wins).</task>
  </step>

  <step id="2" name="Delete src/simulatte/jobshop/">
    <task>Delete the directory after all moved files are in place and imports updated.</task>
    <task>Update any remaining references in tests/docs.</task>
  </step>

  <step id="3" name="Make Env Explicit Everywhere">
    <task>Update constructors to accept `env` and store `self.env = env`.</task>
    <task>
      Remove:
      <detail>`Singleton` metaclass usage for `ShopFloor`.</detail>
      <detail>Any internal `Environment()` construction in `Server/ShopFloor/Router/PSP/builders/Runner`.</detail>
    </task>
    <task>Update builders to construct one env and pass it to every component.</task>
    <task>Update tests to create `env = Environment()` and pass it through.</task>
  </step>

  <step id="4" name="Enforce Job-Centric Server Semantics + Unified KPIs">
    <task>Keep `ServerPriorityRequest(job=...)` as the request type.</task>
    <task>Ensure that AGV/Warehouse operations are implemented as `Job` subclasses and run through the same request/process path.</task>
    <task>Keep KPI computations consistent across all server types.</task>
  </step>

  <step id="5" name="Add WarehouseStore(Server) + AGVServer(Server)">
    <task>
      Implement `WarehouseStore` with:
      <detail>bay capacity (`capacity=n_bays`)</detail>
      <detail>inventory (`Container` per product)</detail>
      <detail>pick/put service times</detail>
    </task>
    <task>
      Implement `AGVServer` with:
      <detail>travel time function (Warehouse ↔ Server)</detail>
      <detail>minimal trip counters</detail>
    </task>
  </step>

  <step id="6" name="Implement Materials Coordinator + Strict FIFO Stalling">
    <task>Extend `ProductionJob` to include per-operation material requirements (optional).</task>
    <task>Implement `MaterialCoordinator.ensure(job, server, op_index)` as described in §4.7.</task>
    <task>Integrate `ensure()` into shopfloor execution while holding the server resource.</task>
    <task>Add a simple supply process (optional) to allow inventory to replenish over time.</task>
  </step>

  <step id="7" name="Update Builders, Policies, Tests, Docs">
    <task>
      Builders:
      <detail>jobshop-only (no materials)</detail>
      <detail>jobshop + materials (warehouse + AGVs + coordinator)</detail>
    </task>
    <task>Policies: port existing jobshop release/priority policies into root `policies/`.</task>
    <task>
      Tests:
      <detail>Update existing jobshop tests to new module paths.</detail>
      <detail>Add new tests: materials missing stalls job while holding server.</detail>
      <detail>Add new tests: materials at later routing steps.</detail>
      <detail>Add new tests: unified KPI available for machine/warehouse/agv.</detail>
    </task>
    <task>Docs: Update `docs/simulatte-architecture.md` and refactor docs to match actual architecture.</task>
  </step>

  <step id="8" name="Delete Unused Modules (No Legacy)">
    <task>Identify modules unused by the new core.</task>
    <task>
      Delete:
      <detail>old intralogistics implementations (`agv/`, `stores/`, `picking_cell/`, controllers, etc.) if not referenced.</detail>
    </task>
    <task>Remove corresponding tests if they test deleted functionality.</task>
    <task>Re-run test suite.</task>
  </step>

</execution-plan>

## 7) Remaining Open Items

<open-items>
  <item topic="material-representation">
    The exact representation of "general consumables" vs job-specific materials:
    <approach>Start with per-operation `{product: qty}`; later can add flags like `consumable=True`.</approach>
  </item>

  <item topic="partial-deliveries">
    Partial deliveries (split qty across multiple AGVs) are not required initially.
  </item>

  <item topic="transport-priority">
    Priority rules for material transport jobs (initially: FIFO, or derived from blocked production job priority).
  </item>
</open-items>
