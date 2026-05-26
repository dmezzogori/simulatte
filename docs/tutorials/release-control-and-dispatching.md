# Release control and dispatching

Goal: control when jobs enter the shopfloor using release policies and apply dispatching rules for queue ordering.

## 1) Push vs pull systems

In a **push** system, jobs enter the shopfloor immediately upon arrival. Simple, but can lead to high WIP (Work-in-Progress) and long queues.

In a **pull** system, jobs wait in a PSP (Pre-Shop Pool) and are released only when conditions are met (e.g., workload below threshold, server starving). This controls WIP and improves flow times.

```
Push:  Arrivals ────────────────> ShopFloor

Pull:  Arrivals ───> PSP ───> ShopFloor
                      │
              (release policy decides when)
```

## 2) The Pre-Shop Pool

The `PreShopPool` is a pure container with no built-in release logic. It holds jobs and provides events that external processes can monitor.

```python
from simulatte.environment import Environment
from simulatte.psp import PreShopPool
from simulatte.shopfloor import ShopFloor

env = Environment()
shopfloor = ShopFloor(env=env)
psp = PreShopPool(env=env, shopfloor=shopfloor)
```

Key properties:

- `psp.empty`: True if no jobs waiting
- `psp.jobs`: Iterate over waiting jobs (FIFO — First-In-First-Out — order)
- `psp.new_job`: Event that fires when a job is added

## 3) Composing release logic

Release policies are wired to the simulation through callback registration methods on `ShopFloor` and `PreShopPool`:

| Method | Fires when | Use case |
|--------|-----------|----------|
| `psp.on_arrival(callback)` | New job enters PSP | Immediate decisions |
| `shopfloor.on_processing_end(callback)` | Job finishes at a server | Starvation avoidance |

For periodic checks, a `periodic_trigger` process is also available (see below).

Example: compose event-driven callbacks with a periodic trigger:

```python
from simulatte.policies.triggers import periodic_trigger

def my_release_fn(psp):
    """Release oldest job if shopfloor WIP is low."""
    if not psp.empty and len(psp.shopfloor.jobs) < 10:
        job = psp.remove()
        psp.shopfloor.add(job)

def my_starvation_fn(job, server):
    """Release a PSP job when a server might starve.

    Called after a job finishes processing at a server.
    We check if the server is now empty.
    """
    if server.empty and not psp.empty:
        # Find a job that starts at this server
        for candidate in psp.jobs_starting_at(server):
            psp.release(candidate)
            break

def my_arrival_fn(job, psp):
    """Release a job immediately if its first server is idle."""
    if job.servers[0].is_idle:
        psp.release(job)

# Register callbacks
shopfloor.on_processing_end(my_starvation_fn)
psp.on_arrival(my_arrival_fn)
env.process(periodic_trigger(psp, 5.0, my_release_fn))
```

The `psp.release(job)` method is a convenience for `psp.remove(job=job)` followed by `shopfloor.add(job)`.

### Advanced: process-based triggers

For cases that require full SimPy process semantics (e.g., yielding timeouts or composing with other events), lower-level trigger functions are available in `simulatte.policies.triggers`:

| Trigger | Fires when |
|---------|-----------|
| `periodic_trigger` | At regular intervals |
| `on_arrival_trigger` | New job enters PSP |
| `on_completion_trigger` | Job finishes at server |

These are started as SimPy processes and run in an infinite loop:

```python
from simulatte.policies.triggers import on_arrival_trigger, on_completion_trigger

# Process-based equivalents of the callbacks above
env.process(on_arrival_trigger(psp, my_arrival_fn))
env.process(on_completion_trigger(shopfloor, psp, my_starvation_trigger_fn))
```

In most cases the callback APIs (`psp.on_arrival`, `shopfloor.on_processing_end`) are simpler and preferred. Use the process-based triggers when you need to yield SimPy events inside your release logic.

## 4) Using builders

Simulatte provides builder functions for common configurations.

### Immediate release (baseline)

Jobs bypass the PSP entirely. Useful as a baseline for comparison.

```python
from simulatte.builders import build_immediate_release_system
from simulatte.environment import Environment

env = Environment()
_, servers, shopfloor, router = build_immediate_release_system(
    env,
    n_servers=6,
    arrival_rate=1.5,
    service_rate=2.0,
)
env.run(until=1000)

print(f"Jobs completed: {len(shopfloor.jobs_done)}")
print(f"Avg time in system: {shopfloor.average_time_in_system:.2f}")
```

The default parameters (`n_servers`, `arrival_rate`, `service_rate`) reflect a standard benchmark workload — pass explicit values to model other regimes.

Optional parameters:

- `priority_policies`: A callable `(job, server) -> float` used to assign queue priorities (dispatching rules). Lower values are served first. Pass `None` (default) for FIFO ordering.
- `collect_workload`: If `True`, attaches a `CurrentWorkLoadCollector` that records total remaining processing work over time (see [ShopFloor extensibility](shopfloor-extensibility.md#currentworkloadcollector)).

A ready-made immediate release with SPT (Shortest Processing Time) dispatching rule is available:

```python
from simulatte.builders import build_immediate_release_system, spt_priority_policy

_, servers, shopfloor, router = build_immediate_release_system(
    env,
    n_servers=6,
    priority_policies=spt_priority_policy,
)
```

### LUMS COR

**Lancaster University Management School corrected order release** (Thürer et al., 2012 — [DOI](https://doi.org/10.1111/j.1937-5956.2011.01307.x)).

Jobs are released only if adding them keeps corrected WIP at or below a workload norm. Combines periodic checks with starvation avoidance.

```python
from simulatte.builders import build_lumscor_system
from simulatte.environment import Environment

env = Environment()
psp, servers, shopfloor, router = build_lumscor_system(
    env,
    check_timeout=10.0,      # Check every 10 time units
    wl_norm_level=5.0,       # Workload threshold per server
    allowance_factor=2,      # Buffer for due date calculation
)
env.run(until=1000)

print(f"Jobs completed: {len(shopfloor.jobs_done)}")
print(f"Avg time in PSP: {sum(j.time_in_psp for j in shopfloor.jobs_done) / len(shopfloor.jobs_done):.2f}")
```

Key parameters:

- `check_timeout`: Time between periodic release checks
- `wl_norm_level`: Maximum corrected WIP allowed per server
- `allowance_factor`: Multiplier for due date slack (higher = more conservative)
- `collect_workload`: If `True`, attaches a `CurrentWorkLoadCollector` (see [ShopFloor extensibility](shopfloor-extensibility.md#currentworkloadcollector))

Release triggers wired by the builder:

1. **Periodic release** (`periodic_trigger`): every `check_timeout` time units, release PSP jobs (sorted by planned release date) whose corrected WIP fits within the workload norm.
2. **Starvation release** (`shopfloor.on_processing_end`): when a server finishes a job:
    - If the server queue is **empty**, immediately release the PSP candidate with the earliest planned release date.
    - If exactly **one job** remains in the queue, schedule a **postponed release** — the candidate is removed from PSP immediately, but enters the shopfloor after a tiny delay so the queued job acquires the server first.
3. **Starvation avoidance** (`psp.on_arrival`): when a new job enters the PSP and its first server is completely idle (empty queue *and* no job processing), the job is released immediately.

Queue ordering uses a PST (planned slack time) priority policy: jobs with lower PST are served first.

### SLAR

**Superfluous Load Avoidance Release** (Land & Gaalman, 1998 — [DOI](https://doi.org/10.1016/S0925-5273(98)00052-8)).

Event-driven release based on planned slack time (PST). No periodic checks — releases are triggered by job completions at servers.

```python
from simulatte.builders import build_slar_system
from simulatte.environment import Environment

env = Environment()
psp, servers, shopfloor, router = build_slar_system(
    env,
    allowance_factor=3.0,    # Slack per operation
)
env.run(until=1000)

print(f"Jobs completed: {len(shopfloor.jobs_done)}")
```

Key parameters:

- `allowance_factor`: Slack allowance per operation (higher = more buffer time)
- `collect_workload`: If `True`, attaches a `CurrentWorkLoadCollector` (see [ShopFloor extensibility](shopfloor-extensibility.md#currentworkloadcollector))

On every job completion at a server, SLAR evaluates three branches in order:

1. **Empty-queue release**: if the server queue is empty, immediately release the most urgent PSP candidate (lowest PST) to prevent idling.
2. **Urgent-job insertion**: if all queued jobs are non-urgent (positive PST), release from PSP the urgent job (negative PST) with the shortest processing time, minimising disruption to the existing queue.
3. **Postponed starvation avoidance**: if exactly one job remains in the queue, schedule a **delayed release** — the candidate is removed from PSP immediately (to avoid double-selection) but enters the shopfloor after a tiny delay so the queued job acquires the server first.

Additionally, a **starvation avoidance** callback is registered via `psp.on_arrival`: when a new job arrives whose first server is completely idle (empty queue *and* no job processing), it is released immediately.

Queue ordering uses a PST-based priority policy: jobs with lower PST are served first.

### SLAR-Limit

**SLAR with a workload-norm limit on urgent insertion** (Thürer & Stevenson, 2021 — [DOI](https://doi.org/10.1016/j.ijpe.2020.107881)).

Extends classic SLAR by gating the urgent-insertion branch with a workload-norm check: an urgent PSP candidate is released only if its corrected workload contribution ``PT / (i + 1)`` keeps every server in its routing at or below its configured norm. The idle-prevention and drain-safety-net branches are inherited unchanged from SLAR.

```python
from simulatte.builders import build_slar_limit_system
from simulatte.environment import Environment

env = Environment()
psp, servers, shopfloor, router = build_slar_limit_system(
    env,
    allowance_factor=3.0,   # Slack per operation
    wl_norm_level=5.0,      # Workload norm per server
)
env.run(until=1000)

print(f"Jobs completed: {len(shopfloor.jobs_done)}")
```

Key parameters:

- `allowance_factor`: Slack allowance per operation (higher = more buffer time)
- `wl_norm_level`: Workload norm applied uniformly to every server. An urgent PSP candidate is released only if adding its corrected contribution keeps every server in its routing at or below this level.
- `collect_workload`: If `True`, attaches a `CurrentWorkLoadCollector` (see [ShopFloor extensibility](shopfloor-extensibility.md#currentworkloadcollector))

**How it differs from SLAR:** when the urgent-insertion branch fires, SLAR releases the urgent PSP candidate with the shortest processing time unconditionally. SLAR-Limit iterates urgent candidates in ascending SPT order and releases the *first* that fits within all server workload norms. If no urgent candidate fits, the branch returns without releasing — the drain-safety-net may still fire on the same event.

**Requirements:** SLAR-Limit requires `CorrectedWIPStrategy` on the shopfloor (set automatically by the builder).

### Dispatching rules

Dispatching rules (priority policies) are ``(job, server) -> float`` callables that determine queue ordering (lower = more urgent). Simulatte provides reusable rules in the `simulatte.dispatching_rules` package:

```python
from simulatte.dispatching_rules import planned_slack_time

# Create a PST rule with per-operation queue-time allowance k=2.0
pst = planned_slack_time(allowance=2.0)

# Use it with a Router, builders, or individual ProductionJob
router = Router(..., priority_policies=pst)
```

`planned_slack_time` returns ``inf`` for servers outside the job's routing or already exited — making it safe for priority comparisons and ``min()`` calls.

## 5) Comparing builder-based systems

Run the builder-based systems and compare:

```python
from simulatte.builders import (
    build_immediate_release_system,
    build_lumscor_system,
    build_slar_system,
    build_slar_limit_system,
)
from simulatte.environment import Environment
from simulatte.runner import Runner

def run_system(builder_fn, builder_kwargs, until=1000):
    def builder(*, env):
        return builder_fn(env, **builder_kwargs)

    def extract(system):
        psp, servers, shopfloor, router = system
        return {
            "jobs_done": len(shopfloor.jobs_done),
            "avg_time_in_system": shopfloor.average_time_in_system,
            "avg_utilization": sum(s.utilization_rate for s in servers) / len(servers),
        }

    # progress=None (default) auto-enables tqdm on TTY; set False to disable
    runner = Runner(builder=builder, seeds=range(5), parallel=False, extract_fn=extract)
    return runner.run(until=until)

# Compare
immediate = run_system(build_immediate_release_system, {"n_servers": 6, "arrival_rate": 1.5})
lumscor = run_system(build_lumscor_system, {"check_timeout": 10, "wl_norm_level": 5, "allowance_factor": 2})
slar = run_system(build_slar_system, {"allowance_factor": 3})
slar_limit = run_system(build_slar_limit_system, {"allowance_factor": 3, "wl_norm_level": 5})

policies = [("Immediate", immediate), ("LumsCor", lumscor), ("SLAR", slar), ("SLAR-Limit", slar_limit)]
for name, results in policies:
    avg_tis = sum(r["avg_time_in_system"] for r in results) / len(results)
    print(f"{name}: avg time in system = {avg_tis:.2f}")
```

## Notes

- Multiple callbacks and triggers can coexist on the same PSP and shopfloor.
- `psp.on_arrival` callbacks run synchronously during `psp.add()`, before the SimPy `new_job` event fires. Process-based listeners via `on_arrival_trigger` resume after.
- `shopfloor.on_processing_end` callbacks run after the server is released (`servers_exit_at` is stamped and `job.previous_server` is available).
- LUMS COR and SLAR-Limit require `CorrectedWIPStrategy` on the shopfloor (set automatically by the builder).
- SLAR is purely event-driven (no periodic trigger).

## 6) Additional policies

Simulatte also provides two additional release policies:

### ConWIP (Constant Work-In-Process)

ConWIP maintains a shop-wide WIP cap based on job count. Jobs are released from the PSP whenever the shopfloor job count drops below the cap, with selection by earliest due date (EDD).

```python
from simulatte.policies.conwip import ConWIP
from simulatte.policies.triggers import on_completion_trigger

conwip = ConWIP(wip_cap=12)
psp = PreShopPool(env=env, shopfloor=shopfloor)
psp.on_arrival(conwip.on_arrival_release)
env.process(on_completion_trigger(shopfloor, psp, conwip.on_completion_release))
```

Reference: Spearman, M. L., Woodruff, D. L. & Hopp, W. J. (1990). *CONWIP: a pull alternative to kanban*. International Journal of Production Research, 28(5), 879-894.

### Continuous Release

Continuous Release is a workload-controlled policy where jobs may be released at any moment when a server's corrected workload drops below its norm. It uses corrected aggregate load: the contribution at routing position *i* is PT / (*i* + 1). Requires `CorrectedWIPStrategy` on the shopfloor.

```python
from simulatte.policies.continuous_release import ContinuousRelease
from simulatte.policies.triggers import on_completion_trigger
from simulatte.shopfloor import CorrectedWIPStrategy

shopfloor.set_wip_strategy(CorrectedWIPStrategy())
cr = ContinuousRelease(wl_norm={server1: 5.0, server2: 5.0}, allowance_factor=2)
psp = PreShopPool(env=env, shopfloor=shopfloor)
psp.on_arrival(cr.on_arrival_release)
env.process(on_completion_trigger(shopfloor, psp, cr.on_completion_release))
```

Reference: Fernandes, N. O. & Carmo-Silva, S. (2011). *Workload control under continuous order release*. International Journal of Production Economics, 131(1), 257-262.

## 7) Dynamic priorities

A job's priority comes from `job.priority_policy`, which simulatte calls
as `policy(job, server)` and which returns a float (lower = more urgent).
This policy is re-evaluated on every dispatch decision: every time a new
job enters a server's queue and every time a job releases a server. Three
patterns are supported first-class:

- **Time-dependent policies** — the value depends on `env.now` (e.g. planned
  slack time, which decreases as the simulation progresses).
- **Policy reassignment** — `job.priority_policy = new_fn` at any time
  reorders the job's position in any queue it is currently waiting in.
- **Mutable external state** — the policy reads from shared state owned by
  user code (e.g. a dispatcher's score table); updates to that state become
  visible at the next dispatch decision.

### Contract

`priority_policy(job, server)` must be a **deterministic function of
`(job, server, current simulation state)`**: repeated calls at the same
`env.now` with the same external state must return the same value. Do not
consume RNG inside the policy and do not mutate state from inside the
policy. If a policy violates this contract, the simulation still runs but
queue ordering becomes unspecified.

### Cost

simulatte calls `priority_policy` once per queued request per dispatch
decision, so the per-event cost scales linearly with the queue length.
Keep policies cheap.

### Example

```python
state = {"A": 10.0, "B": 20.0}

job_a = ProductionJob(
    env=env, sku="A", servers=[server], processing_times=[3.0],
    due_date=1000.0, priority_policy=lambda j, s: state["A"],
)
job_b = ProductionJob(
    env=env, sku="B", servers=[server], processing_times=[3.0],
    due_date=1000.0, priority_policy=lambda j, s: state["B"],
)

# Both queue with A ahead of B.
shopfloor.add(job_a)
shopfloor.add(job_b)

# Mutate the shared state; at the next dispatch decision the queue
# is re-sorted and B will be served before A.
state["A"] = 30.0
state["B"] = 5.0
```

`Server.sort_queue()` can also be called explicitly if you want the new
order to be observable immediately (between events).

Runnable end-to-end examples live in
`tests/core/test_server.py::TestDynamicPriorityRefresh`.

## Next

- [ShopFloor extensibility](shopfloor-extensibility.md)
