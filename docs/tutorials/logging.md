# Logging

Goal: trace simulation events, debug behavior, and analyze what happened during a run.

Each `Environment` has a built-in logger that:

- Automatically includes simulation time in output
- Supports JSON or text format
- Maintains an in-memory history buffer for post-run analysis
- Allows per-component filtering

## 1) Basic usage

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

## 2) Log levels

Set the global log level to control verbosity:

```python
from simulatte.logger import SimLogger

SimLogger.set_level("WARNING")  # Only WARNING and ERROR
SimLogger.set_level("DEBUG")    # Everything
SimLogger.set_level("INFO")     # Default
```

## 3) Built-in events and component logs

The production components (`Server`, `ShopFloor`, `PreShopPool`, `Router`) write no log messages. They emit typed
events on `env.bus`; subscribe before the run to collect them:

```python
from simulatte.environment import Environment
from simulatte.server import JobQueued
from simulatte.shopfloor import JobFinished

env = Environment()
seen = []
env.bus.subscribe(seen.append, (JobQueued, JobFinished))  # or "*" for every domain event

# ... build the system and run ...

for event in seen:
    print(event.t, event.type_name, event.job)
```

Each event has `t` (simulation time), `seq` (emission order) and `type_name`, plus the payload fields listed below.
Ids in payloads are entity ids: `job-0`, `job-1`, ... for jobs, and the `name=` given to a component or its generated
id. The builders name their entities `wc-0`, `wc-1`, ..., `shopfloor`, `router` and `psp`, each with the optional
`prefix=`.

The intralogistics components still emit structured **DEBUG** log messages. These are **best-effort** (not a stable
API): message text and `extra` keys may change between releases. The in-memory `env.log_history` only records
messages that pass the current global log level, so enable DEBUG to collect them:

```python
from simulatte.logger import SimLogger

SimLogger.set_level("DEBUG")

# ... run ...

warehouse_messages = env.log_history.query(component="Warehouse")
for e in warehouse_messages:
    print(e.timestamp, e.message, e.extra)
```

### Catalog

Notes:

- Some “started” log messages may be emitted before a blocking wait (e.g., waiting for inventory/AGV capacity); use
  timestamps and follow-up messages to infer actual durations.

#### Server

Classes in `simulatte.server`:

| Event type | Class | Payload |
| --- | --- | --- |
| `job.queued` | `JobQueued` | `job`, `server`, `priority`, `queue_length` (waiting requests when the job joins, itself included) |
| `job.granted` | `JobGranted` | `job`, `server` |
| `job.queue_left` | `JobQueueLeft` | `job`, `server`, `reason` (`cancelled`) |
| `job.released` | `JobReleased` | `job`, `server` |
| `server.queue_reordered` | `ServerQueueReordered` | `server` |

#### ShopFloor

Classes in `simulatte.shopfloor`, in the order they occur for each job:

| Event type | Class | Payload |
| --- | --- | --- |
| `shopfloor.entered` | `ShopFloorEntered` | `job`, `shopfloor` |
| `operation.started` | `OperationStarted` | `job`, `server`, `op_index`, `processing_time`, `planned_end` (after the before-operation hooks and material delivery) |
| `operation.completed` | `OperationCompleted` | `job`, `server`, `op_index`, `processing_time` |
| `shopfloor.wip_updated` | `ShopFloorWipUpdated` | `shopfloor`, `changes` (server id to its new WIP) |
| `job.finished` | `JobFinished` | `job`, `shopfloor`, `makespan`, `lateness`, `total_queue_time` |

After `job.finished`, the metrics collector and the `on_job_finished` callbacks, the job is retired:
`entity.retired` (class `EntityRetired` in `simulatte.entities`) removes it from `env.entities.live()`.
`shop_floor.jobs_done` still holds it.

#### PreShopPool

Classes in `simulatte.psp`:

| Event type | Class | Payload |
| --- | --- | --- |
| `psp.entered` | `PspEntered` | `job`, `psp`, `position` |
| `psp.exited` | `PspExited` | `job`, `psp`, `reason` (`released`, `postponed` or `removed`) |

#### Router

The router has no events of its own. A new job appears as `entity.created` (class `EntityCreated` in
`simulatte.entities`, with `kind == "job"`), followed in the same instant by `psp.entered` or `shopfloor.entered`.

#### Warehouse (`component="Warehouse"`)

| Event | Message (example) | `extra` keys |
| --- | --- | --- |
| Pick start | `[{name}] Pick started (sku={sku.id}, qty={quantity})` | none beyond component |
| Pick completed | `[{name}] Pick completed (sku={sku.id}, qty={quantity})` | none beyond component |
| Put start | `[{name}] Put started (sku={sku.id}, qty={quantity})` | none beyond component |
| Put completed | `[{name}] Put completed (sku={sku.id}, qty={quantity})` | none beyond component |

#### AGV (`component="AGV"`)

| Event | Message (example) | `extra` keys |
| --- | --- | --- |
| State transition | `{agv_id} OLD_STATE -> NEW_STATE` | none beyond component |

#### MaterialCoordinator

`MaterialCoordinator` is a protocol used by `ShopFloor`; it emits no built-in event family, and a custom implementation may emit its own events under a documented component name.

## 4) Log to file

```python
env = Environment(log_file="simulation.log")
env.info("This goes to the file")
```

## 5) JSON format

For structured logging (useful for log aggregation tools):

```python
env = Environment(log_file="simulation.json", log_format="json")
env.info("Job completed", component="Server", job_id="J1", duration=5.2)
```

Output:

```json
{"sim_time": 0.0, "sim_time_formatted": "00d 00:00:0.00", "wall_time": "2025-12-25T12:00:00+00:00", "level": "INFO", "message": "Job completed", "component": "Server", "extra": {"job_id": "J1", "duration": 5.2}}
```

## 6) Query log history

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

## 7) Component filtering

Disable noisy components:

```python
env.logger.disable_component("Warehouse")  # Silence Warehouse logs
env.logger.enable_component("Warehouse")   # Re-enable
```

## 8) Per-simulation logs with Runner

When running parallel experiments, each simulation can write to its own log file:

```python
from pathlib import Path

from simulatte.builders import build_immediate_release_system
from simulatte.runner import Runner

def builder(*, env):
    env.info("Simulation starting", component="Main")
    return build_immediate_release_system(env=env)

def extract(system):
    _psp, servers, shopfloor, _router, _policy = system
    avg_util = sum(s.utilization_rate for s in servers) / len(servers)
    return {"jobs_done": len(shopfloor.jobs_done), "avg_utilization": avg_util}

if __name__ == "__main__":
    runner = Runner(
        builder=builder,
        seeds=range(10),
        parallel=True,
        extract_fn=extract,
        log_dir=Path("logs"),  # Each run gets its own file
        log_format="json",  # Optional: use JSON format
        # progress=None (default) auto-enables tqdm on TTY; set False to disable
    )

    results = runner.run(until=1000)
    print(results)
    # Creates: logs/sim_0000_seed_0.log, logs/sim_0001_seed_1.log, ...
```

## 9) Context manager

For explicit resource cleanup:

```python
with Environment(log_file="run.log") as env:
    # ... run simulation ...
    pass
# Log file handler is automatically closed
```

## 10) Logging inside components

Add logging to your custom components:

```python
from simulatte.server import Server

class MyServer(Server):
    def process_job(self, job, processing_time):
        self.env.debug(
            f"Processing {job.sku}",
            component=self.__class__.__name__,
            job_id=job.id,
            processing_time=processing_time,
        )
        yield from super().process_job(job, processing_time)
        self.env.info(
            f"Completed {job.sku}",
            component=self.__class__.__name__,
            job_id=job.id,
            processing_time=processing_time,
        )
```

## Next

- [Troubleshooting](../guides/troubleshooting.md)
