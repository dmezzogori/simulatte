# Production Planning & Control

Simulatte's production planning layer models a job-shop manufacturing system: jobs arrive stochastically, wait in a pre-shop pool until a release policy admits them, then flow through a sequence of servers whose queues are re-ordered by a dispatching rule before every scheduling decision. This separation between arrival, release, and dispatching lets you benchmark different WIP-control and priority strategies on the same simulated shopfloor.

## Core concepts

### Jobs & routing

A `ProductionJob` is the fundamental unit of work. Each job carries a server sequence (its routing), a processing time per operation, and an absolute due date. Due dates are specified as simulation-clock time (`env.now + offset` at creation), not as relative offsets. The job records timestamps at each routing step for makespan and lateness analysis.

### Servers & queues

A `Server` extends SimPy's `PriorityResource`. It can process one or more jobs concurrently (set by `capacity`) and tracks queue length and utilization rate. Queue sorting is dynamic: before every dispatch, `Server.sort_queue` re-evaluates the priority policy for every waiting job and re-orders the queue accordingly. You can visualise queue and utilisation history with `plot_qt()` and `plot_ut()` if `collect_time_series=True` is set at construction.

### Pre-shop pool & release control

The `PreShopPool` is a pure buffer queue — it holds jobs that have arrived but have not yet been admitted to the shopfloor. Release logic lives entirely in external policies that observe the pool and decide when to move jobs to the `ShopFloor`. Available policies include workload-based rules (LumsCor, SLAR, SlarLimit), a load-norm approach (Draco), a constant-WIP cap (ConWIP), and a workload-controlled continuous release (ContinuousRelease). Policies are wired to the pool via trigger functions (`periodic_trigger`, `on_arrival_trigger`, `on_completion_trigger`) from `simulatte.policies`. See the [Release Policies API](../api/release-policies.md) for the full reference.

### Dispatching

Once a job is on the shopfloor and waiting at a server, a dispatching rule determines its priority relative to other waiting jobs. Rules range from stateless (SPT — shortest processing time, EDD — earliest due date, FCFS — first come first served) to parameterised (ATC — apparent tardiness cost, slack-based rules) to system-state rules (Focus, which uses real-time shopfloor data). A priority policy is a plain callable `(job, server) -> float` that you pass at construction — no subclassing required. See the [Dispatching Rules API](../api/dispatching-rules.md) for the full catalogue.

### WIP & metrics

The `ShopFloor` continuously tracks work-in-process (WIP) using a pluggable `WIPStrategy` (`StandardWIPStrategy` or `CorrectedWIPStrategy`). It also maintains exponential moving average (EMA) metrics for throughput and flow time. Hooks (`on_before_operation`, `on_after_operation`) let you inject setup times, logging, or custom measurements at every operation without subclassing.

## Where to go next

- [Tutorials](../tutorials/index.md): copy/paste-friendly walkthroughs covering the core building blocks, release control, dispatching, and multi-run experiments.
- [Examples](../examples/index.md): runnable end-to-end scripts for Draco release and Focus dispatching.
- [Core API](../api/core.md): full reference for `ShopFloor`, `Server`, `ProductionJob`, `PreShopPool`, `Router`, and `Runner`.
