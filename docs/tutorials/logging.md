# Logging

Goal: trace simulation events, debug behavior, and analyze what happened during a run.

Each `Environment` has a built-in logger that:

- Automatically includes simulation time in output
- Supports JSON or text format
- Maintains an in-memory history buffer for post-run analysis
- Allows per-component filtering

## Basic usage

```python
from simulatte.environment import Environment

env = Environment()
env.run(until=100)

env.info("Simulation checkpoint", component="Main")
env.debug("Detailed info", component="Server", job_id="J1")
env.warning("Queue getting long", component="Router", queue_size=15)
env.error("Timeout exceeded", component="AGV")
```

Output (to stderr by default):

```
00d 00:01:40.00 | INFO     | Main         | Simulation checkpoint
00d 00:01:40.00 | DEBUG    | Server       | Detailed info
00d 00:01:40.00 | WARNING  | Router       | Queue getting long
00d 00:01:40.00 | ERROR    | AGV          | Timeout exceeded
```

## Log levels

Set the global log level to control verbosity:

```python
from simulatte.logger import SimLogger

SimLogger.set_level("WARNING")  # Only WARNING and ERROR
SimLogger.set_level("DEBUG")    # Everything
SimLogger.set_level("INFO")     # Default
```

## Built-in component logs (best-effort)

Simulatte’s built-in components emit structured **DEBUG** events for tracing and post-run analysis. These are
**best-effort** (not a stable API): message text and `extra` keys may change between releases.

Important: the in-memory `env.log_history` only records events that pass the current global log level, so to
collect built-in component events you must enable DEBUG:

```python
from simulatte.logger import SimLogger

SimLogger.set_level("DEBUG")
```

After a run, query by component:

```python
server_events = env.log_history.query(component="Server")
for e in server_events:
    print(e.timestamp, e.message, e.extra)
```

### Per-component event catalog

Notes:

- Most job-related messages include `job.id[:8]` for readability; the full id is in `extra["job_id"]`.
- `server_id` / `warehouse_id` refer to the component’s internal `_idx` (usually set when registered on a `ShopFloor`).
- Some “started” events may be emitted before a blocking wait (e.g., waiting for inventory/AGV capacity); use timestamps
  and follow-up events to infer actual durations.

#### Server (`component="Server"`)

| Event | Message (example) | `extra` keys |
| --- | --- | --- |
| Queue entry | `Job ab12cd34 entered queue` | `job_id`, `server_id`, `priority`, `queue_length`, `sku` |
| Processing start | `Job ab12cd34 processing started` | `job_id`, `server_id`, `processing_time` |
| Release | `Job ab12cd34 released` | `job_id`, `server_id`, `time_at_server` |

#### ShopFloor (`component="ShopFloor"`)

| Event | Message (example) | `extra` keys |
| --- | --- | --- |
| Job entry | `Job ab12cd34 entered shopfloor` | `job_id`, `sku`, `wip_total`, `jobs_count` |
| Operation queued | `Job ab12cd34 queued at server 0` | `job_id`, `server_id`, `op_index` |
| Operation completed | `Job ab12cd34 completed op at server 0` | `job_id`, `server_id`, `op_index`, `processing_time` |
| Job finished | `Job ab12cd34 finished` | `job_id`, `sku`, `makespan`, `lateness`, `total_queue_time` |

#### Router (`component="Router"`)

| Event | Message (example) | `extra` keys |
| --- | --- | --- |
| Job created | `Job ab12cd34 created` | `job_id`, `sku`, `routing_length`, `due_date`, `total_processing_time` |
| Routed to PSP | `Job ab12cd34 routed to PSP` | `job_id`, `destination` |
| Routed to ShopFloor | `Job ab12cd34 routed to ShopFloor` | `job_id`, `destination` |

#### PreShopPool (`component="PreShopPool"`)

| Event | Message (example) | `extra` keys |
| --- | --- | --- |
| PSP entry | `Job ab12cd34 entered PSP` | `job_id`, `sku`, `psp_size`, `due_date` |
| PSP release | `Job ab12cd34 released from PSP` | `job_id`, `time_in_psp`, `psp_size_after` |

#### WarehouseStore (`component="WarehouseStore"`)

| Event | Message (example) | `extra` keys |
| --- | --- | --- |
| Pick start | `Pick started: 2x A` | `product`, `quantity`, `inventory_before`, `warehouse_id` |
| Pick completed | `Pick completed: 2x A` | `product`, `quantity`, `pick_time`, `inventory_after` |
| Put start | `Put started: 5x B` | `product`, `quantity`, `inventory_before`, `warehouse_id` |
| Put completed | `Put completed: 5x B` | `product`, `quantity`, `put_time`, `inventory_after` |

#### AGV (`component="AGV"`)

| Event | Message (example) | `extra` keys |
| --- | --- | --- |
| Travel start | `Travel started: Server(id=0) -> Server(id=1)` | `agv_id`, `origin_id`, `destination_id`, `travel_time` |
| Travel completed | `Travel completed: arrived at Server(id=1)` | `agv_id`, `destination_id`, `trip_count` |

#### MaterialCoordinator (`component="MaterialCoordinator"`)

| Event | Message (example) | `extra` keys |
| --- | --- | --- |
| Delivery triggered | `Material delivery triggered for job ab12cd34` | `job_id`, `server_id`, `op_index`, `materials` |
| Delivery completed | `Material delivery completed for job ab12cd34` | `job_id`, `server_id`, `delivery_time`, `total_deliveries` |
| Warehouse pick requested | `Warehouse pick requested: 2x A` | `product`, `quantity`, `warehouse_id` |
| AGV selected | `AGV selected: AGV-0` | `agv_id`, `workload`, `agv_count` |
| AGV transport started | `AGV transport started: 2x A` | `agv_id`, `product`, `quantity`, `destination_id` |

## Log to file

```python
env = Environment(log_file="simulation.log")
env.info("This goes to the file")
```

## JSON format

For structured logging (useful for log aggregation tools):

```python
env = Environment(log_file="simulation.json", log_format="json")
env.info("Job completed", component="Server", job_id="J1", duration=5.2)
```

Output:

```json
{"sim_time": 0.0, "sim_time_formatted": "00d 00:00:0.00", "wall_time": "2025-12-25T12:00:00+00:00", "level": "INFO", "message": "Job completed", "component": "Server", "extra": {"job_id": "J1", "duration": 5.2}}
```

## Query log history

The environment keeps a ring buffer of recent log events (default: 1000 entries):

```python
env = Environment(log_history_size=500)

# ... run simulation ...

# Get all ERROR events
errors = env.log_history.query(level="ERROR")

# Get Server events between t=100 and t=200
server_events = env.log_history.query(
    component="Server",
    since=100.0,
    until=200.0,
)

# Iterate all events
for event in env.log_history:
    print(f"{event.timestamp}: {event.message}")
```

## Component filtering

Disable noisy components:

```python
env.logger.disable_component("Router")  # Silence Router logs
env.logger.enable_component("Router")   # Re-enable
```

## Per-simulation logs with Runner

When running parallel experiments, each simulation can write to its own log file:

```python
from pathlib import Path
from simulatte.runner import Runner

def builder(*, env):
    env.info("Simulation starting")
    # ... build system ...
    return system

runner = Runner(
    builder=builder,
    seeds=range(10),
    parallel=True,
    extract_fn=extract,
    log_dir=Path("logs"),        # Each run gets its own file
    log_format="json",           # Optional: use JSON format
)

results = runner.run(until=1000)
# Creates: logs/sim_0000_seed_0.log, logs/sim_0001_seed_1.log, ...
```

## Context manager

For explicit resource cleanup:

```python
with Environment(log_file="run.log") as env:
    # ... run simulation ...
    pass
# Log file handler is automatically closed
```

## Logging inside components

Add logging to your custom components:

```python
class MyServer(Server):
    def process(self, job):
        self.env.debug(
            f"Processing {job.sku}",
            component=self.__class__.__name__,
            job_id=job.id,
        )
        yield from super().process(job)
        self.env.info(
            f"Completed {job.sku}",
            component=self.__class__.__name__,
            duration=job.processing_times[0],
        )
```
