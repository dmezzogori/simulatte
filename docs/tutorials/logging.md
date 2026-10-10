# Logging

Goal: trace simulation events, debug behavior, and analyze what happened during a run.

`env.debug()`, `env.info()`, `env.warning()` and `env.error()` emit `log` events (class `LogEvent` in
`simulatte.events`) on the environment's event bus, stamped with the simulation time. Log sinks
(`simulatte.logsinks`) subscribe to them and write them out. Every `Environment` attaches default sinks:

- a text sink writing to stderr, or to `log_file`, in text or JSON format
- an in-memory history for post-run analysis (`env.log_history`)
- an SQLite database when `log_db_path` is given (`env.log_db`)

Each sink has its own level and component filters.

## 1) Basic usage

```python
from simulatte.environment import Environment

env = Environment(log_level="DEBUG")
env.run(until=100)

env.info("Simulation checkpoint", component="Main")
env.debug("Detailed info", component="Server", job_id="J1")
env.warning("Queue getting long", component="Router", queue_size=15)
env.error("Timeout exceeded", component="AGV")
```

Output (to stderr by default):

```
0.0d 00:01:40.00 | INFO     | Main         | Simulation checkpoint
0.0d 00:01:40.00 | DEBUG    | Server       | Detailed info
0.0d 00:01:40.00 | WARNING  | Router       | Queue getting long
0.0d 00:01:40.00 | ERROR    | AGV          | Timeout exceeded
```

## 2) Log levels

Each environment has its own level, applied to its default sinks; the default is `INFO`:

```python
quiet = Environment(log_level="WARNING")  # Only WARNING and ERROR
verbose = Environment(log_level="DEBUG")  # Everything, including every domain event
```

At `DEBUG` the text, JSON and SQLite sinks also write every domain event (see section 3) as a `DEBUG` record, with
the namespace of its type as component:

```
0.0d 00:00:0.00 | DEBUG    | job          | job.queued job='job-0' server='wc-0' priority=0.0 queue_length=1
```

This makes every emitting site build its event, so a `DEBUG` run is slower. At any other level, and for the
history, sinks listen to `log` events only and domain events are not built for them.

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

The intralogistics components (`FleetCoordinator`, AGVs, traffic managers, warehouses, charging stations and
parking areas) emit typed events as well. The fleet coordinator keeps its warnings and errors as log messages
(`component="FleetCoordinator"`); query them from the in-memory history after the run:

```python
# ... run ...

fleet_problems = env.log_history.query(component="FleetCoordinator")
for e in fleet_problems:
    print(e.t, e.level, e.message)
```

### Catalog

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

#### Release policies

Class in `simulatte.policies`. `ConWIP`, `ContinuousRelease`, `Draco`, `LumsCor`, `Slar` and `SlarLimit` emit a
decision event for each action they take on a job (Draco can emit two for one job, see below); it carries no state
change (the `psp.exited` and `shopfloor.entered` events that follow do):

| Event type | Class | Payload |
| --- | --- | --- |
| `policy.decision` | `PolicyDecision` | `policy` (the policy's class name), `job`, `action` (`release`, `postpone` or `force_pin`) |

A `postpone` is a release after a short delay: the job leaves the pool at once (`psp.exited` with reason `postponed`,
location `transit`) and enters the shop floor 0.001 time units later. Draco emits `force_pin` when it pins the winner
at the server's queue head, followed by `release` when the winner came from the pool.

#### Router

The router has no events of its own. A new job appears as `entity.created` (class `EntityCreated` in
`simulatte.entities`, with `kind == "job"`), followed in the same instant by `psp.entered` or `shopfloor.entered`.

#### FleetCoordinator, AGVs and transfer orders

Classes in `simulatte.intralogistics.events`. The coordinator keeps its warnings and errors as log messages
(`component="FleetCoordinator"`, for example "No path from ..."); everything else is an event:

| Event type | Class | Payload |
| --- | --- | --- |
| `fleet.agv_added` | `FleetAgvAdded` | `fleet`, `agv` (at coordinator construction, per AGV) |
| `fleet.pending_changed` | `FleetPendingChanged` | `fleet`, `order`, `op` (`added` or `removed`), `index` |
| `order.status_changed` | `OrderStatusChanged` | `order`, `status`, `previous`, `reason` (for example `dispatched`, `picked`, `delivered`, `cancelled`, `travel_failed`) |
| `order.assigned` | `OrderAssigned` | `order`, `agv` |
| `order.unassigned` | `OrderUnassigned` | `order`, `agv` (re-queue after an interruption, mission cleanup) |
| `agv.state_changed` | `AgvStateChanged` | `agv`, `state`, `previous` (every `AGV.transition_to`) |
| `agv.placed` | `AgvPlaced` | `agv`, `node`, `previous` (created at a bound node, or `current_node` assigned directly) |
| `agv.move_started` | `AgvMoveStarted` | `agv`, `from_node`, `to_node`, `t_end`, `motion` (the speed profile's motion description), `loaded` |
| `agv.move_ended` | `AgvMoveEnded` | `agv`, `node`, `battery` |
| `agv.move_interrupted` | `AgvMoveInterrupted` | `agv`, `node` (the node the AGV stays at), `reason` |
| `agv.load_changed` | `AgvLoadChanged` | `agv`, `load` (SKU id to quantity, or null) |
| `agv.battery_changed` | `AgvBatteryChanged` | `agv`, `battery` (recharge or battery swap) |
| `agv.stranded` | `AgvStranded` | `agv`, `node`, `reason` (`no_reachable_charger` or `insufficient_after_charging`) |

An order retires (`entity.retired`) when it reaches `COMPLETED`, `CANCELLED` or `FAILED`, after its mission cleanup.

#### Traffic, warehouses, charging stations and parking areas

Classes in `simulatte.intralogistics.events`:

| Event type | Class | Payload |
| --- | --- | --- |
| `traffic.reserved` | `TrafficReserved` | `agv`, `node` (initial placement, or `enter_node` granted) |
| `traffic.released` | `TrafficReleased` | `agv`, `node` (`leave_node`) |
| `traffic.wait_started` | `TrafficWaitStarted` | `agv`, `node` (the next node the AGV means to enter), `reason` (`node_occupied`, `path_delay` or `deadlock_backoff`) |
| `traffic.wait_ended` | `TrafficWaitEnded` | `agv`, `node`, `reason` (`granted`, `cancelled`, `interrupted` or `elapsed`) |
| `warehouse.inventory_changed` | `WarehouseInventoryChanged` | `warehouse`, `sku`, `level`, `delta` (positive for a put, negative for a pick) |
| `warehouse.slot_changed` | `WarehouseSlotChanged` | `warehouse`, `in_use` (a pick or put slot acquired or released) |
| `charging.started` | `ChargingStarted` | `station`, `agv`, `mode` (`recharge` or `swap`; the AGV was granted a slot) |
| `charging.ended` | `ChargingEnded` | `station`, `agv`, `mode` (the slot was released) |
| `charging.pool_changed` | `ChargingPoolChanged` | `station`, `swap_pool` (a swap took a charged battery, or one was returned) |
| `parking.entered` | `ParkingEntered` | `area`, `agv` (`ParkingArea.enter`) |
| `parking.left` | `ParkingLeft` | `area`, `agv` (`ParkingArea.leave`) |

Only the `ResourceBasedTrafficManager` reserves nodes; with the default free traffic, several AGVs share a node and
no reservation events occur. `FleetCoordinator` never calls `ParkingArea.enter` or `leave` itself.

#### MaterialCoordinator

`MaterialCoordinator` is a protocol used by `ShopFloor`; it emits no built-in event family, and a custom implementation may emit its own events under a documented component name.

## 4) Log to file

```python
env = Environment(log_file="simulation.log")
env.info("This goes to the file")
env.close()
```

The file is opened once, in append mode, when the environment is created, and closed by `env.close()`.

## 5) JSON format

For structured logging (useful for log aggregation tools):

```python
env = Environment(log_file="simulation.json", log_format="json")
env.info("Job completed", component="Server", job_id="J1", duration=5.2)
```

Output:

```json
{"sim_time": 0, "sim_time_formatted": "0.0d 00:00:0.00", "wall_time": "2025-12-25T12:00:00+00:00", "seq": 0, "kind": "log", "type": "log", "level": "INFO", "message": "Job completed", "component": "Server", "extra": {"job_id": "J1", "duration": 5.2}}
```

A domain event written at `DEBUG` has `"kind": "domain"`, its event type as `type`, the namespace of the type as
`component`, and its payload as `data`.

## 6) Query log history

`env.log_history` keeps the most recent log events (default: 1000 entries). Each one is a `LogEvent` with `t`
(simulation time), `seq`, `level`, `message`, `component` and `extra`:

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
    print(f"{event.t}: {event.message}")
```

## 7) Query the SQLite log

With `log_db_path`, log events are also stored in an SQLite database, in the `events` table with the columns
`env_id, seq, t, kind, type, level, component, message, data_json`. `env.log_db` is the SQLite sink:

```python
env = Environment(log_db_path="runs.db")

# ... run simulation ...

errors = env.log_db.query(level="ERROR", since=100.0, limit=50)  # LogEvent objects of this environment
rows = env.log_db.execute_sql(
    "SELECT component, COUNT(*) AS n FROM events WHERE env_id = ? GROUP BY component",
    (env.log_db.env_id,),
)
env.close()
```

Several environments can share one database file; `env_id` tells their rows apart. `query()` returns the `log`
records only; at `DEBUG` the domain events are stored as rows with `kind = 'domain'` and their payload in
`data_json`. Query before `env.close()`: closing the environment closes the connection.

## 8) Component filtering and custom sinks

Component filters belong to each sink. `env.sinks` lists the sinks attached to the environment:

```python
for sink in env.sinks:
    sink.disable_component("FleetCoordinator")  # Silence the fleet's warnings and errors
env.log_history.enable_component("FleetCoordinator")  # Re-enable it in the history only
```

More sinks attach with `attach(env)`; `env.close()` closes them too:

```python
from simulatte.logsinks import HistorySink, JsonSink, TextSink

errors = HistorySink(10_000, level="ERROR").attach(env)
TextSink("fleet.log", components=["FleetCoordinator"]).attach(env)
JsonSink("trace.jsonl", level="DEBUG", exclude=["agv"]).attach(env)  # log events and domain events, without AGVs
```

## 9) Per-simulation logs with Runner

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
        log_level="INFO",  # Optional: the level of every run's environment
        # progress=None (default) auto-enables tqdm on TTY; set False to disable
    )

    results = runner.run(until=1000)
    print(results)
    # Creates: logs/sim_0000_seed_0.log, logs/sim_0001_seed_1.log, ...
```

## 10) Context manager

For explicit resource cleanup:

```python
with Environment(log_file="run.log") as env:
    # ... run simulation ...
    pass
# The log file and the other sinks are closed
```

## 11) Logging inside components

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

The keyword arguments become the `extra` of the record. With `Environment(debug=True)` they must be wire values
(numbers, strings, booleans, `None`, lists and string-keyed maps); anything else raises, so a log call cannot put
an arbitrary object into a trace or a digest.

## Next

- [Events, traces and KPIs](../guides/events-and-traces.md)
- [Troubleshooting](../guides/troubleshooting.md)
