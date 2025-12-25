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
0.0d 00:01:40.00 | INFO     | Main         | Simulation checkpoint
0.0d 00:01:40.00 | DEBUG    | Server       | Detailed info
0.0d 00:01:40.00 | WARNING  | Router       | Queue getting long
0.0d 00:01:40.00 | ERROR    | AGV          | Timeout exceeded
```

## Log levels

Set the global log level to control verbosity:

```python
from simulatte.logger import SimLogger

SimLogger.set_level("WARNING")  # Only WARNING and ERROR
SimLogger.set_level("DEBUG")    # Everything
SimLogger.set_level("INFO")     # Default
```

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
