# Simulatte Refactor Analysis & Implementation Plan

**Document Version**: 1.0  
**Date**: 2025-12-23  
**Status**: Analysis Complete, Ready for Implementation Review  

---

## Executive Summary

This document provides a detailed analysis and refined implementation plan for refactoring Simulatte to integrate the discrete-event simulation (DES) core from `rl-ppc`. The refactor aims to:

1. **Replace** existing Simulatte core with rl-ppc DES foundation (Environment, Server, ShopFloor, Router, PSP, policies)
2. **Preserve** warehouse/AGV domain capabilities from Simulatte
3. **Model** Warehouse and AGV as `Server` subclasses for unified KPIs and scheduling
4. **Enable** simulations of job-shop flows with optional material-handling delivery steps

Based on architectural clarification questions, key design decisions have been finalized:
- Material handling modeled as metadata-driven materialization policy (Option C)
- Individual AGVs as Server instances (Q2)
- WarehouseStore as Server with input/output bays (Q3)
- Per-AGV distance configuration (Q4)
- Test semantics ported to new API (Q5)
- Original builders preserved (Q6)

---

## 1. Architectural Decisions Based on User Feedback

### 1.1 Material Handling Semantics (Option C)

**Decision**: Jobs carry metadata `needs_materials: bool` (or richer structure). A separate "materialization policy" coordinates material delivery independently of the job's server routing.

**Implementation Pattern**:
```python
class Job:
    def __init__(self, ..., material_requirements: MaterialRequirements | None = None):
        self.material_requirements = material_requirements  # or None

class MaterialRequirements:
    warehouse: WarehouseServer
    location: WarehouseLocationServer
    priority: int  # influences AGV dispatch and warehouse queuing
```

**Materialization Policy** (registered as SimPy process):
```python
def materialization_process(shopfloor: ShopFloor, warehouse: WarehouseServer, agvs: list[AGVServer]):
    """Continuously monitors jobs needing materials and coordinates delivery."""
    while True:
        # Check for jobs waiting for materials
        for job in shopfloor.jobs:
            if job.material_requirements and not job.material_ready:
                # Select AGV, coordinate warehouse retrieval, stage at input
                schedule_delivery(job, warehouse, agvs)
        yield env.timeout(check_interval)
```

**Benefits**:
- Separation of concerns: Job routing focuses on processing; materialization focuses on logistics
- Jobs can be released to shopfloor before materials arrive (optional starvation triggers)
- Materialization can be enabled/disabled globally

### 1.2 AGV Fleet Architecture (Individual AGVs)

**Decision**: Each AGV is a `Server` instance with capacity=1.

**Implementation**:
```python
class AGVServer(Server):
    def __init__(self, *, kind: AGVKind, speed: float, 
                 travel_time_fn: Callable[[Location, Location], float],
                 env: Environment):
        super().__init__(capacity=1)
        self.kind = kind
        self.speed = speed
        self.travel_time_fn = travel_time_fn
        self.status = AGVStatus.IDLE
        self.current_location: Location | None = None
        self._missions: list[AGVMission] = []
        self._trips: list[AGVTrip] = []
    
    def process_job(self, job: TransportJob, travel_time: float) -> ProcessGenerator:
        """Process a transport job with travel time."""
        self.status = AGVStatus.TRAVELING
        yield env.timeout(travel_time)
        self._record_trip(job.source, job.destination, travel_time)
        self.status = AGVStatus.IDLE
        self.worked_time += travel_time
```

**Benefits**:
- Preserves individual AGV status tracking, travel history
- Natural integration with Server queue/utilization metrics
- AGV dispatch policy simply selects from available AGVServer instances

### 1.3 WarehouseStore as Server

**Decision**: Warehouse is a `Server` representing input/output bays. Processes compete for bay access. Internal location management is encapsulated.

**Implementation Pattern**:
```python
class WarehouseServer(Server):
    def __init__(self, *, n_input_bays: int, n_output_bays: int,
                 locations: list[WarehouseLocationServer], env: Environment):
        super().__init__(capacity=n_input_bays + n_output_bays)  # total bay capacity
        self.input_bays = n_input_bays
        self.output_bays = n_output_bays
        self.locations = locations  # child servers
        self._storage_policy: StoragePolicy = FirstAvailableStoragePolicy()
    
    def process_job(self, job: StorageJob, load_time: float) -> ProcessGenerator:
        """Store or retrieve unit load at a location."""
        # Storage policy selects location
        location = self._storage_policy.select(job, self.locations)
        
        # Process the storage/retrieval
        yield env.timeout(load_time)
        
        # Update location state
        location.unit_load = job.unit_load
        self.worked_time += load_time

class WarehouseLocationServer(Server):
    """Leaf server for physical storage slot."""
    def __init__(self, *, location_id: str, capacity: int = 1, env: Environment):
        super().__init__(capacity=capacity)
        self.location_id = location_id
        self.unit_load: UnitLoad | None = None
```

**Benefits**:
- Bays modeled as capacity constraint on WarehouseServer
- Location servers track individual slot utilization
- Clear separation: WarehouseServer coordinates, WarehouseLocationServer stores

### 1.4 Distance/Travel Time Configuration

**Decision**: Per-AGV callable configuration.

**Implementation**:
```python
def euclidean_travel_time_fn(speed: float) -> Callable[[Location, Location], float]:
    """Factory for Euclidean distance-based travel time."""
    def fn(origin: Location, destination: Location) -> float:
        dx = origin.x - destination.x
        dy = origin.y - destination.y
        distance = (dx**2 + dy**2) ** 0.5
        return distance / speed
    return fn

# Usage
agv1 = AGVServer(kind=AGVKind.FEEDING, speed=1.5, 
                  travel_time_fn=euclidean_travel_time_fn(1.5), env=env)
agv2 = AGVServer(kind=AGVKind.REPLENISHMENT, speed=2.0,
                  travel_time_fn=euclidean_travel_time_fn(2.0), env=env)
```

---

## 2. Design Pattern: Configurable History Recording

### 2.1 Problem Statement

With hundreds of WarehouseLocationServer instances, storing per-server queue/utilization histories can cause significant memory overhead. We need a globally toggleable mechanism.

### 2.2 Proposed Solution: Strategy + Null Object Pattern

**Architecture**:
```
HistoryRecorder (interface)
    ├── RealHistoryRecorder: Stores histories in Server
    └── NullHistoryRecorder: No-op implementation
```

**Global Configuration**:
```python
@dataclass
class SimulationConfig:
    """Global configuration for simulation behavior."""
    enable_queue_history: bool = True
    enable_utilization_history: bool = True
    history_recorder: HistoryRecorder | None = None
    
    def __post_init__(self):
        if self.history_recorder is None:
            if self.enable_queue_history or self.enable_utilization_history:
                self.history_recorder = RealHistoryRecorder(self)
            else:
                self.history_recorder = NullHistoryRecorder()
```

**HistoryRecorder Interface**:
```python
class HistoryRecorder(Protocol):
    """Protocol for recording Server metrics."""
    
    def record_queue_update(self, server: Server, time: float, queue_length: int) -> None:
        """Record queue length change."""
        ...
    
    def record_utilization_update(self, server: Server, time: float, is_busy: bool) -> None:
        """Record utilization change."""
        ...
    
    def get_queue_history(self, server: Server) -> list[tuple[float, int]]:
        """Retrieve queue history."""
        ...
    
    def get_utilization_history(self, server: Server) -> list[tuple[float, int]]:
        """Retrieve utilization history."""
        ...
```

**Real Implementation**:
```python
class RealHistoryRecorder:
    def __init__(self, config: SimulationConfig):
        self._config = config
        self._queue_histories: dict[int, list[tuple[float, int]]] = defaultdict(list)
        self._utilization_histories: dict[int, list[tuple[float, int]]] = defaultdict(list)
    
    def record_queue_update(self, server: Server, time: float, queue_length: int) -> None:
        if self._config.enable_queue_history:
            self._queue_histories[id(server)].append((time, queue_length))
    
    def record_utilization_update(self, server: Server, time: float, is_busy: bool) -> None:
        if self._config.enable_utilization_history:
            status = int(is_busy)
            history = self._utilization_histories[id(server)]
            if not history or history[-1][1] != status:
                history.append((time, status))
    
    def get_queue_history(self, server: Server) -> list[tuple[float, int]]:
        return self._queue_histories.get(id(server), [])
    
    def get_utilization_history(self, server: Server) -> list[tuple[float, int]]:
        return self._utilization_histories.get(id(server), [])
```

**Null Implementation**:
```python
class NullHistoryRecorder:
    """No-op recorder that stores nothing."""
    
    def record_queue_update(self, server: Server, time: float, queue_length: int) -> None:
        pass
    
    def record_utilization_update(self, server: Server, time: float, is_busy: bool) -> None:
        pass
    
    def get_queue_history(self, server: Server) -> list[tuple[float, int]]:
        return []
    
    def get_utilization_history(self, server: Server) -> list[tuple[float, int]]:
        return []
```

**Server Integration**:
```python
class Server(PriorityResource):
    def __init__(self, *, capacity: int, env: Environment, 
                 config: SimulationConfig | None = None):
        self.env = env
        self.config = config or SimulationConfig()
        self.history_recorder = self.config.history_recorder
        super().__init__(self.env, capacity)
        
        # Metrics
        self.worked_time: float = 0
        self._last_queue_level: int = 0
        self._last_queue_timestamp: float = 0
    
    def _record_queue(self) -> None:
        """Update queue recording."""
        queue_length = len(self.queue)
        self.history_recorder.record_queue_update(
            self, self.env.now, queue_length
        )
        
        # Track time at previous level
        duration = self.env.now - self._last_queue_timestamp
        self._queue_history[self._last_queue_level] += duration
        self._last_queue_level = queue_length
        self._last_queue_timestamp = self.env.now
    
    def _record_utilization(self) -> None:
        """Update utilization recording."""
        is_busy = self.count > 0 or len(self.queue) > 0
        self.history_recorder.record_utilization_update(
            self, self.env.now, is_busy
        )
```

**Usage Example**:
```python
# Lightweight mode: no history recording
config_no_hist = SimulationConfig(
    enable_queue_history=False,
    enable_utilization_history=False
)

# Standard mode: both histories
config_full = SimulationConfig(
    enable_queue_history=True,
    enable_utilization_history=True
)

# Mixed mode: only utilization
config_util_only = SimulationConfig(
    enable_queue_history=False,
    enable_utilization_history=True
)

servers = [Server(capacity=1, env=env, config=config_no_hist) for _ in range(1000)]
```

**Memory Savings Analysis**:
- Assume 1000 locations, 1000 simulation hours, history recorded every minute
- Full history: ~1.2M data points × 16 bytes = ~19 MB per metric
- Null recorder: 0 bytes
- This pattern scales linearly with location count

---

## 3. Environment Model: Singleton vs Explicit Injection Analysis

### 3.1 Comparison Matrix

| Aspect | Singleton (rl-ppc style) | Explicit Injection (Simulatte style) |
|--------|---------------------------|--------------------------------------|
| **Verbosity** | Low: `Environment()` automatically returns shared instance | High: must pass `env` parameter everywhere |
| **Test Isolation** | Fragile: requires `Singleton.clear()` between tests | Robust: each test creates fresh env |
| **Parallel Experiments** | Risky: must ensure `Singleton.clear()` in worker processes | Safe: each process has independent env |
| **Thread Safety** | None: singleton not thread-safe by default | Better: env per-thread/process |
| **API Clarity** | Implicit: hard to trace where env comes from | Explicit: dependency graph visible |
| **Composition** | Rigid: global state encourages tight coupling | Flexible: env is a composable dependency |

### 3.2 Hybrid Approach Recommendation

Given the user's hesitation about Singleton and the need for parallel experiment safety, I recommend a **Context-Dependent Pattern**:

```python
class Environment(simpy.Environment):
    """Simulation environment with optional singleton behavior."""
    
    _instance: ClassVar[Environment | None] = None
    _singleton_mode: ClassVar[bool] = False
    
    def __new__(cls, *args, **kwargs):
        """Create shared instance if in singleton mode."""
        if cls._singleton_mode and cls._instance is not None:
            return cls._instance
        instance = super().__new__(cls)
        if cls._singleton_mode:
            cls._instance = instance
        return instance
    
    @classmethod
    def enable_singleton_mode(cls) -> None:
        """Enable singleton behavior for backward compatibility."""
        cls._singleton_mode = True
    
    @classmethod
    def disable_singleton_mode(cls) -> None:
        """Disable singleton behavior for explicit injection."""
        cls._singleton_mode = False
        cls._instance = None
    
    @classmethod
    def clear(cls) -> None:
        """Clear singleton instance if in singleton mode."""
        if cls._singleton_mode:
            cls._instance = None
    
    def step(self) -> None:
        """Step with keyboard interrupt handling."""
        try:
            super().step()
        except KeyboardInterrupt:
            raise StopSimulation
```

**Usage Patterns**:

**Backward Compatibility (Singleton)**:
```python
Environment.enable_singleton_mode()
env1 = Environment()
env2 = Environment()  # Same instance
assert env1 is env2
```

**Modern Usage (Explicit)**:
```python
Environment.disable_singleton_mode()
env = Environment()
servers = [Server(capacity=1, env=env) for _ in range(6)]
```

**Runner Adaptation**:
```python
class Runner[S, T]:
    def __init__(self, *, builder: Builder[S], seeds: Sequence[int], 
                 singleton_mode: bool = False, **kwargs):
        self.builder = builder
        self.seeds = seeds
        self.singleton_mode = singleton_mode
        # ... other kwargs
    
    def _run_single(self, seed_until: tuple[int, float]) -> T:
        # Setup mode for this run
        if self.singleton_mode:
            Environment.enable_singleton_mode()
            Environment.clear()
        else:
            Environment.disable_singleton_mode()
        
        ShopFloor.servers = []  # Reset server registry
        env = Environment()
        seed, until = seed_until
        random.seed(seed)
        system = self.builder(env=env)  # Pass env explicitly
        env.run(until=until)
        return self.extract_fn(system)
```

**Recommendation**: Start with **explicit mode as default** (set `singleton_mode=False` in Runner default). Keep singleton mode available for rl-ppc compatibility but document as legacy.

---

## 4. Updated Implementation Plan

### 4.1 Prerequisites

Before starting workstreams:
1. Agree on Environment model (hybrid approach recommended)
2. Approve HistoryRecorder design
3. Confirm SimulationConfig structure

### 4.2 Workstream Refined Sequence

| # | Workstream | Dependencies | Estimated Effort | Notes |
|---|------------|---------------|------------------|-------|
| 0 | **Environment & Config** | None | 1 day | Implement hybrid Environment + SimulationConfig + HistoryRecorder |
| 1 | Core Import & Renaming | #0 | 2 days | Import rl-ppc modules, strip RL code, adapt to hybrid env |
| 2 | Server Unification | #1 | 3 days | Port Server to use HistoryRecorder, add extension hooks |
| 3 | Material Server Layer | #2 | 5 days | Implement MaterialServer, AGVServer, WarehouseServer, WarehouseLocationServer |
| 4 | Flow Integration | #3 | 4 days | Materialization policy, Job metadata, Router extension |
| 5 | Policies & Builders | #4 | 3 days | AGV dispatch, warehouse policies, new material-aware builders |
| 6 | Metrics & Observability | #5 | 2 days | AGV-specific metrics, ensure unified KPIs work |
| 7 | Test Migration | Ongoing | 4 days | Port test semantics to new API |
| 8 | Docs & Examples | #6 | 2 days | Update architecture docs, add examples |
| 9 | Cleanup | #8 | 1 day | Remove legacy code, finalize packaging |

**Total**: ~25 days of focused development

---

## 5. Detailed Workstreams

### Workstream 0: Environment & Configuration Foundation

**Deliverables**:
- `simulatte.environment.Environment` with hybrid singleton/explicit support
- `simulatte.config.SimulationConfig` dataclass
- `simulatte.monitoring.HistoryRecorder` protocol + implementations
- `simulatte.monitoring.NullHistoryRecorder` no-op class

**Key Implementation Decisions**:
- Default to explicit injection mode
- Config is thread-safe (immutable dataclass)
- History recorder injection point: Server.__init__

**Acceptance Criteria**:
- `Environment()` creates new instance in explicit mode
- `Environment()` returns shared instance in singleton mode
- `SimulationConfig(enable_queue_history=False)` disables recording
- Null recorder has zero overhead

---

### Workstream 1: Core Import & Renaming

**Deliverables**:
- `simulatte.jobshop.server.Server` (ported from rl-ppc)
- `simulatte.jobshop.server.FaultyServer`
- `simulatte.jobshop.server.InspectionServer`
- `simulatte.jobshop.shopfloor.ShopFloor`
- `simulatte.jobshop.job.Job`
- `simulatte.jobshop.router.Router`
- `simulatte.jobshop.psp.PreShopPool`
- `simulatte.jobshop.policies.LumsCor`, `Slar`, `Draco`
- `simulatte.jobshop.distributions`
- `simulatte.jobshop.runner.Runner`

**Key Changes**:
- Remove `agents/`, `training/`, Gym wrappers
- Adapt Server to use `HistoryRecorder` from WS0
- Remove `Singleton` metaclass usage (use hybrid Environment)
- Update imports to use new namespace

**Acceptance Criteria**:
- All rl-ppc tests pass with new imports
- Server correctly uses HistoryRecorder
- No RL dependencies remain

---

### Workstream 2: Server Unification

**Deliverables**:
- Server with extension hooks (`on_request`, `on_release`)
- Server respects `SimulationConfig` for history recording
- `MaterialServer` abstract base class

**Key Extensions**:
```python
class MaterialServer(Server):
    """Base for material-handling servers."""
    
    def __init__(self, *, capacity: int, env: Environment,
                 load_time: float = 0, unload_time: float = 0,
                 config: SimulationConfig | None = None):
        super().__init__(capacity=capacity, env=env, config=config)
        self.load_time = load_time
        self.unload_time = unload_time
    
    def on_request(self, request: ServerPriorityRequest) -> None:
        """Hook called when server is requested."""
        pass
    
    def on_release(self, release: Release) -> None:
        """Hook called when server is released."""
        pass
```

**Acceptance Criteria**:
- `MaterialServer` is abstract (cannot be instantiated directly)
- Subclasses can override hooks for custom behavior
- Server correctly records/disables history based on config

---

### Workstream 3: Material Server Layer

**Deliverables**:
- `simulatte.material_handling.agv.AGVServer`
- `simulatte.material_handling.warehouse.WarehouseServer`
- `simulatte.material_handling.warehouse.WarehouseLocationServer`
- `simulatte.material_handling.transport.TransportJob` (or reuse Job)

**AGVServer Implementation**:
```python
class AGVServer(MaterialServer):
    def __init__(self, *, kind: AGVKind, speed: float,
                 travel_time_fn: Callable[[Location, Location], float],
                 env: Environment, config: SimulationConfig | None = None):
        super().__init__(capacity=1, env=env, config=config,
                       load_time=1.0, unload_time=1.0)
        self.kind = kind
        self.speed = speed
        self.travel_time_fn = travel_time_fn
        self.status = AGVStatus.IDLE
        self.current_location: Location | None = None
    
    def process_job(self, job: TransportJob, travel_time: float) -> ProcessGenerator:
        self.status = AGVStatus.TRAVELING
        yield env.timeout(travel_time)
        self.current_location = job.destination
        self.status = AGVStatus.IDLE
        self.worked_time += travel_time
```

**WarehouseServer Implementation**:
- Capacity = sum of input + output bays
- Owns list of `WarehouseLocationServer` instances
- `StoragePolicy` selects location (injectable)
- `RetrievalPolicy` selects location (injectable)

**WarehouseLocationServer Implementation**:
- Simple Server with capacity=1 (by default)
- Stores `UnitLoad | None`
- Tracks occupancy state

**Acceptance Criteria**:
- AGVServer maintains status/location
- WarehouseServer coordinates child locations
- History recording is globally toggleable

---

### Workstream 4: Flow Integration

**Deliverables**:
- Extended `Job` with `material_requirements: MaterialRequirements | None`
- `MaterialRequirements` dataclass
- `MaterializationPolicy` protocol
- `DefaultMaterializationPolicy` implementation
- Router extension to support material handling flag

**Job Extension**:
```python
@dataclass
class MaterialRequirements:
    warehouse: WarehouseServer
    location: WarehouseLocationServer | None = None
    priority: int = 0
    ready: bool = False  # Set True when staged

class Job:
    # ... existing fields ...
    
    def __init__(self, *, ..., 
                 material_requirements: MaterialRequirements | None = None):
        # ... existing init ...
        self.material_requirements = material_requirements
        self.material_ready = material_requirements is None
```

**Materialization Policy**:
```python
class MaterializationPolicy(Protocol):
    def materialize(self, job: Job, warehouse: WarehouseServer, 
                    agv_controller: AGVController) -> None:
        """Coordinate material delivery for a job."""
        ...

class DefaultMaterializationPolicy:
    def __init__(self, check_interval: float = 1.0):
        self.check_interval = check_interval
    
    def materialize(self, job: Job, warehouse: WarehouseServer,
                    agv_controller: AGVController) -> None:
        """Select AGV, retrieve from warehouse, stage at first server."""
        agv = agv_controller.best_agv()
        location = warehouse.storage_policy.select(job)
        transport_job = TransportJob(
            source=location,
            destination=job.first_server.input_location,
            unit_load=location.unit_load
        )
        # AGV processes transport job
        warehouse.env.process(agv.process_job(transport_job, ...))
        job.material_ready = True
```

**Acceptance Criteria**:
- Jobs can carry material requirements
- Materialization policy coordinates delivery
- Jobs can enter shopfloor before materials (optional flag)

---

### Workstream 5: Policies & Builders

**Deliverables**:
- `AGVDispatchPolicy` protocol + `LeastBusyAGV`, `ShortestETA` implementations
- `StoragePolicy` protocol + `FirstAvailable`, `NearestLocation` implementations
- `RetrievalPolicy` protocol + `FirstAvailable` implementation
- New builders: `build_system_with_materials`, `build_warehouse_only`

**Policies as Callables** (following Simulatte convention):
```python
def least_busy_agv_dispatch(agvs: Sequence[AGVServer], 
                            job: TransportJob) -> AGVServer:
    return min(agvs, key=lambda a: (len(a.queue), a.worked_time))

def shortest_eta_dispatch(agvs: Sequence[AGVServer],
                        job: TransportJob) -> AGVServer:
    """Select AGV with minimum travel time to source."""
    def eta(agv: AGVServer) -> float:
        return agv.travel_time_fn(agv.current_location, job.source)
    return min(agvs, key=eta)
```

**Builders**:
```python
def build_system_with_materials(
    *,
    enable_materials: bool = True,
    warehouse_config: WarehouseConfig,
    agv_config: AGVConfig,
    config: SimulationConfig = SimulationConfig(),
    env: Environment,
) -> System:
    """Build system with optional material handling."""
    # Build job shop servers
    servers = [Server(capacity=1, env=env, config=config) for _ in range(6)]
    shopfloor = ShopFloor(config=config)
    
    # Build warehouse if materials enabled
    warehouse = None
    agvs = []
    if enable_materials:
        warehouse = WarehouseServer(**warehouse_config, env=env, config=config)
        agvs = [AGVServer(**aconf, env=env, config=config) 
                  for aconf in agv_config.agvs]
        
        # Register materialization process
        materialization = DefaultMaterializationPolicy()
        env.process(materialization.run(shopfloor, warehouse, agvs))
    
    # Build router
    router = Router(servers=servers, psp=psp, ..., env=env)
    
    return System(servers=servers, shopfloor=shopfloor, warehouse=warehouse, 
                 agvs=agvs, router=router)
```

**Acceptance Criteria**:
- Policies work as simple callables
- Builders support enable_materials flag
- Original builders remain unchanged

---

### Workstream 6: Metrics & Observability

**Deliverables**:
- AGV-specific KPIs: travel time, loaded vs empty miles, mission count
- Warehouse saturation, location occupancy
- Unified metrics API across Server types

**Metrics Collection**:
```python
class AGVServer(Server):
    # ... existing ...
    
    @property
    def travel_distance(self) -> float:
        return sum(t.distance for t in self._trips)
    
    @property
    def loaded_distance(self) -> float:
        return sum(t.distance for t in self._trips if t.loaded)
    
    @property
    def empty_distance(self) -> float:
        return self.travel_distance - self.loaded_distance

class WarehouseServer(Server):
    # ... existing ...
    
    @property
    def occupancy_rate(self) -> float:
        occupied = sum(1 for loc in self.locations if loc.unit_load is not None)
        return occupied / len(self.locations)
    
    @property
    def input_queue_length(self) -> int:
        return len(self.queue[:self.input_bays])
    
    @property
    def output_queue_length(self) -> int:
        return len(self.queue[self.input_bays:])
```

**Acceptance Criteria**:
- All Server types expose consistent metrics interface
- Plotting utilities work for any Server type
- History recording toggle affects all metrics

---

### Workstream 7: Test Migration

**Deliverables**:
- All Simulatte tests ported to new API
- rl-ppc tests pass with renamed imports
- New integration tests for material handling

**Migration Strategy**:
1. **Identify test categories**:
   - Job-shop core tests (rl-ppc)
   - AGV/warehouse domain tests (Simulatte)
   - Integration tests

2. **Port semantics**:
   - Keep test intentions unchanged
   - Update imports
   - Update API calls
   - Add `config` parameters where needed

3. **Add new tests**:
   - Materialization policy tests
   - AGV dispatch policy tests
   - History recorder toggle tests
   - Parallel experiment safety tests

**Acceptance Criteria**:
- All existing tests pass (with API updates)
- Coverage remains > 80%
- Parallel mode works correctly

---

### Workstream 8: Docs & Examples

**Deliverables**:
- Updated `simulatte-architecture.md`
- New `material-handling-guide.md`
- Example scripts:
  - `examples/basic_jobshop.py` (pure job-shop)
  - `examples/jobshop_with_warehouse.py` (materials enabled)
  - `examples/warehouse_only.py` (AGV + warehouse only)
  - `examples/parallel_experiments.py` (Runner demonstration)

**Documentation Updates**:
- Architecture diagram with material handling flow
- Configuration guide (SimulationConfig options)
- Migration guide for existing Simulatte users
- API reference for new Server types

**Acceptance Criteria**:
- All examples run without errors
- Docs are consistent with implementation
- Migration path is clear

---

### Workstream 9: Cleanup

**Deliverables**:
- Legacy modules removed or marked deprecated
- Packaging finalized
- Version bump

**Cleanup List**:
- Remove or deprecate old `agv/agv.py` (replaced by `agv_server.py`)
- Remove or deprecate old `stores/warehouse_store.py` (replaced by `warehouse_server.py`)
- Clean up `controllers/` if policies replace them
- Update `__init__.py` exports
- Remove deprecated code paths

**Acceptance Criteria**:
- No duplicate implementations
- Public API is clean
- Package installs correctly

---

## 6. Risk Mitigation Strategy

### 6.1 Identified Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|---------|------------|
| **Scope creep in WS3** | Medium | High | Keep MaterialServer thin; delegate to subclasses |
| **Parallel experiment bugs with hybrid env** | Low | High | Explicit mode default; thorough testing of Runner |
| **Performance degradation with many locations** | Medium | Medium | NullHistoryRecorder; documented best practices |
| **Materialization policy starvation** | Medium | Medium | Optional starvation triggers (pull when server idle) |
| **Test migration uncovers hidden bugs** | High | Low | Incremental migration; run tests after each WS |
| **API confusion between old/new Server** | Medium | Medium | Clear deprecation path; migration guide |

### 6.2 Critical Path Items

1. **WS0**: Environment & Config foundation (blocks all others)
2. **WS1**: Core import (blocks Server unification)
3. **WS3**: Material server layer (blocks flow integration)
4. **WS4**: Materialization policy (blocks material-aware builders)

**Recommendation**: Execute in sequence, but WS7 (test migration) should run in parallel with WS2-6 to catch issues early.

---

## 7. Open Questions & Decisions Needed

### 7.1 Architectural Decisions

| Question | Options | Recommendation |
|----------|----------|----------------|
| **Environment default mode** | Singleton vs Explicit | **Explicit** (safer for parallel) |
| **History recorder default** | Enabled vs Disabled | **Enabled** (backward compatible) |
| **Materialization policy registration** | Auto vs Manual | **Manual** (explicit control) |
| **AGV dispatch policy** | Callable vs Class | **Callable** (follow Simulatte pattern) |

### 7.2 Implementation Details

| Question | Notes | Recommendation |
|----------|-------|----------------|
| **Should Job.extend() for materials?** | Or separate MaterializedJob? | **Extend Job** (simpler API) |
| **Should MaterializationPolicy be process or callable?** | Process = runs forever; Callable = one-shot | **Process** (continuous monitoring) |
| **Should WarehouseServer locations be created via builder?** | Or passed in constructor? | **Constructor** (flexible) |
| **Should history be stored per-instance or centrally?** | Central = easier to clear; Per-instance = more encapsulated | **Central via HistoryRecorder** (flexible clearing) |

---

## 8. Success Criteria (Refined)

### 8.1 Functional Requirements

- [ ] Can run existing rl-ppc job-shop scenarios with renamed imports (API parity)
- [ ] New warehouse+AGV builders produce consistent SimPy runs
- [ ] Unified KPIs (queue length, utilization) available for all Server types
- [ ] History recording is globally toggleable with measurable memory savings
- [ ] Parallel experiments work correctly with explicit environment mode

### 8.2 Non-Functional Requirements

- [ ] No rl-ppc RL dependencies remain in core
- [ ] Code coverage remains > 80%
- [ ] Package installs and imports cleanly
- [ ] Documentation covers new architecture
- [ ] Migration path for existing Simulatte users is clear

---

## 9. Next Steps

1. **Review this document** and confirm architectural decisions
2. **Approve or modify** HistoryRecorder design
3. **Confirm Environment mode** (explicit vs singleton default)
4. **Prioritize workstreams** or adjust sequence
5. **Begin implementation** with WS0

---

**End of Document**
