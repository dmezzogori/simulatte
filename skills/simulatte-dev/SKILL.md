---
name: simulatte:dev
description: >
  Use when building, configuring, debugging, or running simulations with the
  Simulatte discrete-event simulation library. Trigger whenever code imports
  from `simulatte`, references Simulatte classes (Environment, ShopFloor,
  Server, ProductionJob, Router, PreShopPool, Runner, SimulatteEnv), or the
  user asks about job-shop simulation in a Python project. Also trigger when
  the user mentions release policies (LumsCor, SLAR, immediate release), WIP
  strategies, workload control, dispatching rules, starvation avoidance,
  multi-run experiments with seed management, or training RL agents on
  simulations (Gymnasium, reinforcement learning, gym environment). Even if
  the user just says "set up a simulation", "compare scheduling policies", or
  "train an RL agent" in a repo that depends on simulatte, use this skill.
---

# Simulatte Development Guide

Simulatte is a discrete-event simulation framework for production planning
and control built on SimPy. It provides ready-made components for production servers,
release control policies, and multi-run experiments.

This skill helps you write correct Simulatte code. Read
`references/api-reference.md` (in this skill's directory) whenever you need
exact constructor signatures, parameter types, or configuration structures.

> **Intralogistics** (`simulatte.intralogistics`) — warehouses, AGV fleets,
> and material transport — is covered by the `simulatte-intralogistics` skill.
> Use that skill for warehouse layouts, fleet coordination, and transport
> policies.

## Understanding the request

Before writing code, figure out which stage the researcher is at:

| They want to...                        | Start here                          |
|----------------------------------------|-------------------------------------|
| Get a quick baseline simulation        | Use a builder function              |
| Compare release policies               | Run multiple builders via `Runner`  |
| Customize dispatching or hooks         | Manual composition                  |
| Run stochastic experiments             | `Runner` with multiple seeds        |
| Train an RL agent on a simulation      | Gymnasium wrapper section           |
| Analyze or plot results                | Result inspection section           |

## Choosing a release policy

Simulatte ships several system configurations (Immediate, LumsCor, SLAR,
SLAR-Limit, DRACO, plus ConWIP and Continuous Release for manual
composition). The choice depends on what the researcher wants to study.

### Immediate Release (push system)

Jobs enter the shopfloor the moment they arrive — no buffering, no WIP control.
Use this as a **baseline** to show what happens without release control.

```python
from simulatte.builders import build_immediate_release_system

_, servers, shopfloor, router = build_immediate_release_system(env)
```

### LumsCor (workload-controlled pull)

Jobs wait in a Pre-Shop Pool and are released only when adding them would keep
each server's corrected WIP at or below a configured norm. Two triggers run
concurrently: a **periodic** check every `check_timeout` time units, and a
**starvation** trigger that fires on each job completion, releasing a PSP
candidate when the completing server's queue is empty or about to drain (only
one job left).

Use LumsCor when the researcher wants to study **WIP-limiting behavior** or
**workload balancing**. Key parameters to vary in experiments:
- `wl_norm_level`: workload norm per server (higher = more WIP allowed)
- `check_timeout`: release check interval (shorter = more responsive)

```python
from simulatte.builders import build_lumscor_system

psp, servers, shopfloor, router = build_lumscor_system(
    env, check_timeout=10.0, wl_norm_level=5.0, allowance_factor=2,
)
```

`build_lumscor_system` sets `CorrectedWIPStrategy` on the shopfloor, which
discounts downstream workload by position (1st op: full, 2nd: 1/2, 3rd: 1/3).
LumsCor requires this strategy and validates it on each release, but does not
set it itself (see the manual-composition note below).

### SLAR (slack-based pull)

Jobs are released from the PSP on job-completion events and on PSP arrival (via
a starvation-avoidance hook) — no periodic checks. Release decisions are based
on planned slack time (PST): when a server queue empties, the most urgent PSP
candidate is released; when no queued job is urgent, an urgent candidate is
inserted; and when only one job remains queued, a postponed release replenishes
the queue before it drains.

Use SLAR when the researcher wants **due-date-driven, event-triggered release**.
Key parameter to vary:
- `allowance_factor`: slack allowance per operation (higher = more conservative)

```python
from simulatte.builders import build_slar_system

psp, servers, shopfloor, router = build_slar_system(
    env, allowance_factor=3.0,
)
```

### DRACO (non-hierarchical WIP control)

DRACO merges release, authorization, and dispatching into one per-server
decision taken on every job completion. At each completion at server `k`, it
scores every candidate in `Q_k ∪ P_k` by `w^R·R + w^A·A + w^D·D` and dispatches
the maximum; its dispatching term `D` is the FOCUS rule. A PSP winner is forced
to dispatch first via a one-shot per-server flag.

Use DRACO when the researcher wants a **single integrated release+dispatch
policy** rather than separate workload-control and dispatching layers. Key
parameters to vary:
- `wip_target` (`τ`): target shop WIP as a job count (not a workload metric)
- `loop_target` (`ε`): target overlapping loop per server pair
- `total_impact_weights`: `(w^R, w^A, w^D)` — release vs. authorization vs. dispatch

```python
from simulatte.builders import build_draco_system

psp, servers, shopfloor, router = build_draco_system(
    env, wip_target=8, loop_target=4,
)
```

`build_draco_system` also wires `psp.on_arrival(starvation_avoidance)` to avoid
a cold-start deadlock (the decision fires only on completions).

## Build workflow

### Quick path: use a builder

Builders return a `(psp, servers, shopfloor, router)` tuple. For push systems,
`psp` is `None`.

```python
from simulatte.environment import Environment
from simulatte.builders import build_lumscor_system

env = Environment()
psp, servers, shopfloor, router = build_lumscor_system(
    env, check_timeout=10.0, wl_norm_level=5.0, allowance_factor=2,
)
env.run(until=10_000)
```

All builders default to 6 servers, exponential inter-arrivals (rate 1/0.648),
truncated 2-Erlang service times (rate 2.0), and a single SKU ("F1") with
random routing through a subset of servers. Override `n_servers`,
`arrival_rate`, and `service_rate` as needed.

### Manual composition

When the researcher needs custom SKUs, multi-product routing, or non-standard
distributions, build the system by hand. The canonical sequence:

1. Create `Environment`
2. Create `ShopFloor` (optionally with hooks, strategies, collectors)
3. Create `Server` instances (pass `shopfloor=` to auto-register)
4. Create `PreShopPool` if using pull system
5. Create `Router` with distribution configuration
6. If pull system, use callback APIs (`on_arrival`, `on_processing_end`) for
   synchronous reactions, and trigger processes (`env.process(periodic_trigger(...))`)
   for periodic or timed checks
7. Run with `env.run(until=...)`

See `references/api-reference.md` for exact signatures.

## Custom dispatching rules

Priority policies are callables with signature `(job, server) -> float`. Lower
values get higher priority. Pass them to `Router(priority_policies=...)` for
stochastic jobs, or directly to `ProductionJob(priority_policy=...)` for
hand-crafted jobs.

Simulatte ships a full catalog in `simulatte.dispatching_rules`:

```python
from simulatte.dispatching_rules import (
    shortest_processing_time,    # SPT
    earliest_due_date,           # EDD
    operational_due_date,        # ODD
    modified_operational_due_date,  # MODD
    critical_ratio,              # CR
    first_come_first_served,     # FCFS
    planned_slack_time,          # PST factory — call with allowance=k
    slack_per_remaining_operation,  # S/OPN factory — call with allowance=k
)

# Tier-1: pass directly
router = Router(..., priority_policies=shortest_processing_time)

# Tier-2: call the factory first to fix the allowance parameter
pst_rule = planned_slack_time(allowance=2.0)
router = Router(..., priority_policies=pst_rule)
```

`Slar` wires `planned_slack_time(allowance=allowance_factor)` onto the router
automatically on construction. `LumsCor` does **not**: it sorts the PSP by
planned release date and never touches `priority_policies`, so PST dispatching
is added separately by the `build_lumscor_system` builder — when composing
`LumsCor` by hand you must set `router.priority_policies` yourself. You can also
use any dispatching rule with `build_immediate_release_system` via its
`priority_policies=` argument.

**FOCUS (system-state rule).** `simulatte.dispatching_rules.Focus` is a
self-establishing rule combining five weighted mechanisms (SPT, starvation,
slack timing, pacing, WIP balance). Unlike the Tier-1/2 rules it is a class
(it exposes per-mechanism methods and a shared `build_context`), wrapped for
the router by `FocusPriorityRule`. For a ready-made push system that
dispatches with FOCUS, use `build_focus_system(env, focus_weights=...)`. FOCUS
is also DRACO's dispatching component.

See `references/api-reference.md` — "Dispatching Rules" section — for full
signatures and the rule table.

## Operation hooks

Hooks inject logic before or after each processing operation. A hook can be
a **plain function returning None** (for synchronous side-effects) or a
**generator yielding SimPy events** (when the hook needs simulation time).
Both styles can coexist in the same hook list and execute in registration order.

```python
# Sync hook: no delay, just mutates state
def reorder_queue(job, server, op_index, processing_time):
    server.sort_queue()

# Generator hook: consumes simulation time
def setup_time(job, server, op_index, processing_time):
    delay = 2.0 if job.sku == "COMPLEX" else 0.5
    yield server.env.timeout(delay)
```

Pass hooks to `ShopFloor(on_before_operation=..., on_after_operation=...)`.
Multiple hooks can be passed as a list; they execute in order.

## Extension APIs

These APIs let you wire event-driven logic after construction, solving
chicken-and-egg problems where the hook object needs a reference to the
shopfloor or PSP it is being attached to.

### Post-construction hook registration

```python
shopfloor.on_before_operation(hook)      # same signature as constructor hooks
shopfloor.on_after_operation(hook)       # sync or generator, appended in order
shopfloor.on_job_finished(callback)      # callback(job) -> None
shopfloor.on_processing_end(callback)    # callback(job, server) -> None
```

`on_processing_end` fires after the server is released (servers_exit_at is
stamped), once per operation — not only when the job finishes its routing.

### PSP event subscription

```python
psp.on_arrival(callback)  # callback(job, psp) -> None
```

Fires synchronously inside `psp.add()`, before the SimPy `new_job` event.
No `env.process()` priming is needed.

### PSP helpers

```python
psp.release(job)              # remove(job=job) + shopfloor.add(job) in one call
psp.jobs_starting_at(server)  # jobs whose first routing server matches
```

### Server helpers

```python
server.is_idle       # True when no users and no queue
server.current_jobs  # tuple of jobs occupying active server slots
```

### Dispatcher protocol

A dispatcher is any object implementing a subset of:
`on_before_operation`, `on_after_operation`, `on_job_finished`,
`on_processing_end`, `on_psp_arrival`. Only present methods are wired.

```python
class MyDispatcher:
    def on_before_operation(self, job, server, op_index, processing_time):
        server.sort_queue()  # sync hook, returns None

    def on_psp_arrival(self, job, psp):
        # Release immediately if the first routing server is idle
        if job.servers[0].is_idle:
            psp.release(job)

d = MyDispatcher()
shopfloor.attach_dispatcher(d, psp=psp)
```

## Multi-run experiments

Use `Runner` for stochastic experiments across multiple random seeds.

```python
from simulatte.runner import Runner
from simulatte.builders import build_lumscor_system

def builder(*, env):
    return build_lumscor_system(
        env, check_timeout=10.0, wl_norm_level=5.0, allowance_factor=2,
    )

def extract(system):
    psp, servers, shopfloor, router = system
    return {
        "completed": len(shopfloor.jobs_done),
        "avg_tis": shopfloor.average_time_in_system,
        "tardy_pct": sum(1 for j in shopfloor.jobs_done if j.late) / len(shopfloor.jobs_done),
        "avg_util": sum(s.utilization_rate for s in servers) / len(servers),
    }

runner = Runner(
    builder=builder,
    seeds=range(30),
    extract_fn=extract,
    parallel=True,
)
results = runner.run(until=10_000)
```

The `builder` callable must accept `*, env` (keyword-only). The `extract_fn`
receives the full system tuple and returns whatever metrics the researcher
needs. Results are a list, one entry per seed, in seed order.

## Gymnasium wrapper (RL integration)

> **Experimental**: `SimulatteEnv` is in `simulatte.experimental` and may
> change in future releases.

`SimulatteEnv` is a thin Gymnasium ABC that wraps a simulation as a
Gymnasium environment for RL training. Subclass it and implement six
abstract methods — the base class handles `reset()`, `step()`, and
`close()` lifecycle plumbing.

```python
from simulatte.experimental.gymnasium import SimulatteEnv
from gymnasium import spaces
import numpy as np

class MyEnv(SimulatteEnv):
    def __init__(self):
        super().__init__()
        self.observation_space = spaces.Box(low=0, high=np.inf, shape=(4,), dtype=np.float64)
        self.action_space = spaces.Discrete(2)

    def setup(self, *, seed, options):       # build fresh simulation
        ...
    def get_observation(self):               # extract state → numpy array
        ...
    def apply_action(self, action):          # apply action + advance sim
        ...
    def compute_reward(self, action):        # return float reward
        ...
    def is_terminated(self):                 # natural episode end?
        ...
    def is_truncated(self):                  # time budget exceeded?
        ...
```

**Key points:**

- `apply_action()` is where you advance the simulation (e.g.,
  `self.sim_env.run(until=...)`). You control when the simulation pauses.
- `compute_reward(action)` receives the action for action-dependent
  penalties. Access simulation state via `self`.
- `teardown()` (optional) cleans up resources between episodes. Called
  before `setup()` on every `reset()` after the first, and from `close()`.
- `get_info()` (optional) returns a step info dict, called last in
  `step()` — use it for reward decomposition or diagnostics.
- Use `self.np_random` (seeded automatically by Gymnasium) for all
  numpy-based randomness.
- Lifecycle guards raise `RuntimeError` if `step()` is called before
  `reset()` or after episode end.

The wrapper works with Stable-Baselines3, CleanRL, and any
Gymnasium-compatible RL library.

## Result inspection

### Job-level metrics (on completed jobs from `shopfloor.jobs_done`)

| Property              | Description                                          |
|-----------------------|------------------------------------------------------|
| `job.makespan`        | Time from creation to completion                     |
| `job.lateness`        | `finished_at - due_date` (negative = early)          |
| `job.late`            | `True` if finished after due date                    |
| `job.time_in_system`  | Time from first server entry to last server exit     |
| `job.time_in_psp`     | Time spent waiting in Pre-Shop Pool                  |
| `job.total_queue_time` | Sum of queue waits across all servers               |

### Server-level metrics

| Property                    | Description                               |
|-----------------------------|-------------------------------------------|
| `server.utilization_rate`   | Fraction of time busy (0 to 1)            |
| `server.average_queue_length` | Time-weighted average queue length      |
| `server.idle_time`          | Total idle time                           |

### ShopFloor-level metrics

| Property                            | Description                          |
|-------------------------------------|--------------------------------------|
| `shopfloor.average_time_in_system`  | Mean TIS across completed jobs       |
| `shopfloor.jobs_done`              | List of completed jobs               |
| `shopfloor.wip`                    | Current WIP dict `{server: float}`   |
| `shopfloor.maximum_wip_value`      | Peak total WIP observed              |

### EMA metrics (via default `metrics_collector`)

The default `EMAMetricsCollector` tracks smoothed metrics. Access via
`shopfloor.metrics_collector`:

- `ema_makespan`, `ema_tardy_jobs`, `ema_early_jobs`, `ema_in_window_jobs`
- `ema_time_in_psp`, `ema_time_in_shopfloor`, `ema_total_queue_time`

### Time-series plots

Enable with `ShopFloor(collect_time_series=True)`, then:

```python
shopfloor.time_series_collector.plot_wip()
shopfloor.time_series_collector.plot_throughput()
shopfloor.time_series_collector.plot_lateness()
shopfloor.time_series_collector.plot_job_count()
```

## Common pitfalls

**Hook return values must be None or a generator.**
Operation hooks can be plain sync functions (return None) or generators
(yield SimPy events). A hook that returns a non-None, non-generator value
raises `TypeError` at runtime.

**`due_date` is absolute simulation time, not an offset.**
When hand-crafting `ProductionJob`, pass `due_date=env.now + offset`, not just
the offset. The `Router` does this automatically
(`due_date = env.now + due_date_offset_distribution[sku]()`), but manual job
creation requires you to add `env.now` yourself.

**`processing_times` must match `servers` length.**
`ProductionJob(servers=[S1, S2], processing_times=[5.0])` will raise
`ValueError` from `zip(..., strict=True)`. Every server in the routing needs a
corresponding processing time.

**Arrival rate is a rate (lambda), not a mean.**
`arrival_rate=1.5` means 1.5 arrivals per time unit (mean inter-arrival =
0.667). The builders pass it to `random.expovariate(arrival_rate)`. If the
researcher specifies a mean inter-arrival time, convert: `arrival_rate = 1 / mean`.

**LumsCor requires `CorrectedWIPStrategy`.**
If composing a LumsCor system manually (not using the builder), you must call
`shopfloor.set_wip_strategy(CorrectedWIPStrategy())` before running. The
builder does this automatically. Forgetting it raises a `TypeError` at runtime.

**Lambda closure trap in Router config.**
When building `sku_service_times` in a loop, lambdas capture the loop variable
by reference. If the lambda body references that variable, all lambdas end up
using the last value. Use default arguments to capture by value:

```python
rates = {s: idx * 0.5 for idx, s in enumerate(servers)}

# WRONG: every lambda reads rates[server] with the LAST server value
times = {server: lambda: random.expovariate(rates[server]) for server in servers}

# Correct: default argument captures current value
times = {server: lambda s=server: random.expovariate(rates[s]) for server in servers}
```

**Runner `builder` must accept keyword-only `env`.**
The signature must be `def builder(*, env):` — the Runner calls it as
`builder(env=env)`. A positional parameter will cause a `TypeError`.
