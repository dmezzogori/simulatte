# Benchmarks

Overhead benchmarks of spec `specs/2026-10-08-sp1-events-trace-design.md` §14 (budgets of the global design
§C1.9). They compare the current branch with `simulatte==0.12.0`, the last release before the event bus and the
trace, and measure the recording modes of the branch. Results of gate G3 are in `specs/research/sp1-g3-report.md`.

The scripts are not part of the package or of the test suite. They import `simulatte` from the environment of
the interpreter that runs them, so each version is installed in its own virtual environment.

## Files

| File | Purpose |
|---|---|
| `workload_gen.py` | Draws a job-shop workload once and writes it as JSON (arrival, SKU, routing as server indices, processing times, due date per job). |
| `feeder.py` | Builds a 10-server LumsCor job shop with constructors present in both versions and replays a workload through `PreShopPool.add`, without the router and without any random draw. Checks the job and operation counts and fingerprints the trajectory. |
| `run.py` | Times one mode (`none`, `default`, `default_logging`, `bare`, `kpi`, `digest`, `full`) on a workload: warm-up runs, timed runs, optionally in several fresh processes; peak memory in a separate process; in modes `kpi` and `full` the trace size, in mode `full` also the chunk count and cold-seek latency. Writes JSON. |
| `intralogistics.py` | Branch-only workload: `examples/intralogistics_advanced.py` scaled up (20 AGVs, an outbound order every 30 to 60 time units, 20 shifts of 28,800 time units: about 22,000 orders). Same modes and JSON as `run.py`; every mode is reported as a ratio to the branch's own `none`. |
| `compare.py` | Compares two results; exits 1 when both are mode `none` and the median overhead exceeds `--budget + --noise` (`--budget` has no default and is required for such a pair), 2 when the results are not comparable. The Limit column shows the sum and its parts. |
| `bench_sampling.py` | Router-only workload: a `Scenario.pure_job_shop` router generates a fixed number of jobs into a counting sink (no processing); reports jobs and sampler calls per second. |
| `summarize.py` | Markdown tables of results (time, peak memory, trace, seeks, sampling) and of their provenance (commit, logging level, hardware). |
| `strip_debug.py` | Writes a copy of an installed 0.12.0 without its `env.debug(...)` calls, to isolate what SP1 added on the unobserved path; the CI gate's second baseline (D55). |
| `workloads/` | The committed CI-size workloads. |

## Workloads

| File | Jobs | Utilization | Seed | Use |
|---|---|---|---|---|
| `workloads/jobshop10-u90-5k.json` | 5,000 | 0.90 | 1 | CI gate |
| `workloads/jobshop10-u95-5k.json` | 5,000 | 0.95 | 2 | CI gate, congested variant |
| `jobshop10-u90-50k.json` (not committed, 4.8 MB) | 50,000 | 0.90 | 3 | full size, local G3 report |
| `jobshop10-u95-50k.json` (not committed, 4.8 MB) | 50,000 | 0.95 | 4 | full size, congested |

The shop follows the defaults of `Scenario.pure_job_shop(n_servers=10)`: routing length uniform in 1..10 with
distinct servers, truncated 2-Erlang processing times (rate 2, at most 4), Poisson arrivals at the rate of the
target utilization, due date arrival + Uniform(30, 45). LumsCor runs with `check_timeout=5`, norm 6 and allowance
factor 2. Each workload stops 1,000 time units after the last arrival; every job is finished by then.

The full-size files are regenerated with CPython (any 3.11+; PyPy draws different values from the same seed):

```bash
python benchmarks/workload_gen.py --servers 10 --jobs 50000 --util 0.9 --seed 3 > /tmp/jobshop10-u90-50k.json
python benchmarks/workload_gen.py --servers 10 --jobs 50000 --util 0.95 --seed 4 > /tmp/jobshop10-u95-50k.json
```

## Running locally

```bash
# Two environments with the same interpreter and dependency versions.
uv venv /tmp/head --python 3.14 && uv pip install --python /tmp/head .
uv pip freeze --python /tmp/head | grep -v '^simulatte' > /tmp/constraints.txt
uv venv /tmp/base --python 3.14 && uv pip install --python /tmp/base simulatte==0.12.0 --constraint /tmp/constraints.txt

cd benchmarks
W=workloads/jobshop10-u90-5k.json
/tmp/base/bin/python run.py --mode none --workload $W --warmup 1 --repeat 5 --processes 3 --json /tmp/base.json
/tmp/head/bin/python run.py --mode none --workload $W --warmup 1 --repeat 5 --processes 3 --json /tmp/head.json
python compare.py /tmp/base.json /tmp/head.json --budget 0.03 --noise 0.02   # released baseline; PyPy: --noise 0.05

/tmp/head/bin/python run.py --mode full --workload $W --warmup 1 --repeat 5 --json /tmp/full.json
python compare.py /tmp/head.json /tmp/full.json        # ratio only, never gated
/tmp/head/bin/python bench_sampling.py --jobs 20000 --json /tmp/sampling-head.json
python summarize.py /tmp/*.json
```

On PyPy use `--warmup 8`: the JIT settles after about seven runs of the CI workload, and different processes
settle at speeds up to about 10 % apart, which is why the gate pools three processes per side.

## What is measured

- **Wall time** of one run: creating the environment (and, in modes `digest` and `full`, enabling the digest or
  creating a `TraceRecorder` with default chunk limits), building the shop, `env.run(until=horizon)` and
  `env.close()` (which flushes the trace). Loading the workload JSON is excluded. `gc.collect()` runs before each
  run, outside the timing.
- **Observers.** Both versions log at their default level (INFO: `SimLogger` in 0.12.0, the default log sinks on
  the branch, which subscribe to `log` events only). The modes differ in the shop floor's default EMA metrics:

  | Mode | Shop floor metrics | Domain-event subscribers on the branch | Gated |
  |---|---|---|---|
  | `none` | disabled in both versions (`metrics_collector=None` in 0.12.0, which skips the per-job `record` call; `default_metrics=False` on the branch) | none; the feeder fails a run that finds one | yes |
  | `default` | each version's default: `EMAMetricsCollector` called at each completion in 0.12.0, `EMACollector` on the branch, a bus subscriber of `job.finished` (so that event is built) | the default `EMACollector` only | no; budget accepted (D59), report-only until calibrated |
  | `default_logging` | disabled, as in `none` | none | no; budget accepted (D59), report-only until calibrated. Nothing on this shop logs at INFO, so the budget only guards the bus-subscription bookkeeping; measuring logging cost needs a workload that logs at INFO (follow-up) |
  | `bare` (branch only) | disabled | none, and the default log sinks closed, so the bus has no subscription at all | no (diagnostic floor of the logging cost) |
  | `kpi` (branch only) | the default `EMACollector` | `TraceRecorder(level="kpi")` (which enables the digest), the `EMACollector` and a `ShopFloorKPIs` collector | no (ratio against `none`) |
  | `digest`, `full` (branch only) | disabled, as in `none` | the digest or the trace recorder | no (ratio against `none`) |

  Every mode runs with the default log sinks of its version, so `default_logging` is the same configuration as
  `none`: the mode makes it explicit (the feeder checks that the default logging is active) and names its budget,
  and `bare` is what the logging cost is measured against. The branch's sinks take `log` events only and nothing
  on the shop path emits one at INFO, so no domain event is built for them.

  0.12.0 calls `env.debug(...)` at every queue
  entry, release, processing start, PSP entry and exit and shop-floor step, building the f-string and keyword
  arguments before the level check; the branch replaced these calls with events guarded by `env.wants`. That
  difference is part of the comparison (the G3 report quantifies it separately).
- **Equal work.** Both versions replay the same jobs through the same constructors and finish all of them; the
  job and operation counts and a fingerprint of the trajectory (every finished job's due date, pool exit and
  finish time in completion order) must be equal, and `compare.py` refuses results that differ.
- **Peak memory** is the peak RSS (`ru_maxrss`) of a separate process that loads the workload and runs it once;
  `rss_before_mb` is the peak before the run.
- **Intralogistics** (`intralogistics.py`): the layout, SKUs, warehouses, AGV type and strategies of the advanced
  example, with more AGVs, a shorter order interval and a longer horizon. The run is seeded, so every mode does the
  same work (the order count, statuses and a fingerprint of the order trajectory are compared by `compare.py`).
  There is no 0.12.0 side: its intralogistics API differs, so no equivalent-work feeder exists. In `none` the
  coordinator has no `OrderEMACollector` (`default_metrics=False`); `default` keeps it; `kpi` adds a `FleetKPIs`
  collector to it and a `TraceRecorder(level="kpi")`.
- **Seeks** (mode `full`): `Trace.open` on the trace of the last run, then cold `state_at` calls (the reader's
  chunk cache is cleared before each) at uniformly random times, seed 0.
- **Sampling** (`bench_sampling.py`): sampler calls per job are the inter-arrival time, the SKU, the routing, one
  processing time per operation and the due-date offset.

- **Provenance** (C1.9): every result records `commit` (`git rev-parse HEAD` of the benchmark checkout, else
  `GITHUB_SHA`, else `unknown`; for `head` this is the measured branch, since `simulatte_version` still reads
  0.12.0 on both sides), `log_level` (`SimLogger.get_level()` in 0.12.0, the default of
  `Environment(log_level=...)` on the branch) and `hardware` (machine, CPU model, CPU count), besides the workload
  and interpreter.
- **Seeks** default to 200 per run (`--seeks`); the G3 report used 500.

## Baseline: 0.12.0 without its debug calls

0.12.0 builds an f-string and keyword arguments for `env.debug(...)` at every step even at the INFO level; the
branch removed these calls. To measure SP1's own cost on the unobserved path, strip them from a copy and put it
first on `PYTHONPATH`. The CI gate uses this copy as its second baseline (decision D55):

```bash
python strip_debug.py /tmp/base/lib/python3.14/site-packages/simulatte /tmp/stripped
PYTHONPATH=/tmp/stripped /tmp/base/bin/python -c 'import simulatte; print(simulatte.__file__)'   # must be under /tmp/stripped
PYTHONPATH=/tmp/stripped /tmp/base/bin/python run.py --mode none --workload $W --warmup 1 --repeat 5 --processes 3 \
    --label "0.12.0 without env.debug calls" --json /tmp/nolog.json
python compare.py /tmp/nolog.json /tmp/head.json --budget 0.065 --noise 0.02   # PyPy: --budget 0.03 --noise 0.05
```

## CI

Job `bench` in `.github/workflows/ci.yml` runs on CPython 3.14 and PyPy 3.11. It creates three environments: the
branch, `simulatte==0.12.0` (with the branch's dependency versions as constraints) and a stripped copy of that
release (`strip_debug.py` on the installed package, run with `PYTHONPATH`; the job prints `simulatte.__file__` and
fails unless the stripped copy is the one imported). It runs mode `none` on both CI workloads in all three and
compares the branch with each baseline. Modes `default` and `default_logging` run on the u90 workload in all three
and are reported against each baseline without a budget. The budgets are job-level environment variables in `ci.yml` (decision D55):

| Baseline | Budget | Noise, CPython | Noise, PyPy | Limit, CPython | Limit, PyPy |
|---|---|---|---|---|---|
| Released 0.12.0 (the user-facing promise) | 3 % | 2 % | 5 % | 5 % | 8 % |
| 0.12.0 without `env.debug` calls | 6.5 % on CPython, 3 % on PyPy (after tuning, G3 report §9) | 2 % | 5 % | 8.5 % | 8 % |

The job fails when the median mode-`none` overhead exceeds `budget + noise` against either baseline; both
comparisons always run. The step summary has one row per baseline, workload and interpreter, with the limit and
its two parts. Modes `default` and `default_logging` (against each baseline's own), `default_logging` against the branch's `bare`,
modes `kpi`, `digest` and `full` (against the branch's `none`), the sampling benchmark, the intralogistics modes
(against the branch's `none`, at half the local size: `--shifts 10`) and a details table are written to the step
summary without gating, and every result JSON is uploaded as an artifact.
