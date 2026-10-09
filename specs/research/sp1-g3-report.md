# SP1 Gate G3 report: budgets on the slice

Branch `feature/sp1-events-trace`, Task 12, benchmark code at 95e84dc (`benchmarks/`, CI job `bench`). Spec:
`specs/2026-10-08-sp1-events-trace-design.md` §14 and the G3 row of §2; budgets from the global design §C1.9,
targets from §C1.7. Measured on eva, 2026-10-09.

**Verdict.** The G3 gate passes. Mode `none` of the branch is 22–24 % faster than `simulatte==0.12.0` on CPython
3.14 and 35–45 % faster on PyPy 3.11, on the CI-size and full-size workloads. The limit is 3 % plus a noise band
of 2 % (CPython) or 5 % (PyPy). The `full` trace of the 10-server job shop over 50,000 jobs is 75.1 MB, under the
100 MB target. Its cold seeks take 54.6 ms at p95 on CPython and 55.5 ms on PyPy, under the 200 ms target.

**Concern for the controller.** The margin comes from removing 0.12.0's `env.debug(...)` calls, which built
f-strings and keyword arguments on every step even at the default INFO level. To isolate this, I made a copy of
0.12.0 with those calls deleted (diagnostic only, §5.2). Against that copy, the branch's unobserved path costs
**+7.2 % to +9.9 % on CPython**. About half of this is the `env.wants(...)` guards: roughly 46 calls per job.
On PyPy the branch is still 16–23 % faster than the copy. The gate as specified (against the released 0.12.0)
passes with a wide margin. However, the logging rebuild of Task 19 (`default_logging`, ≤ 5 %) will spend part
of that margin. No library code was tuned in this task.

## 1. Methodology

**Equal work across versions (S20).** `benchmarks/workload_gen.py` draws a workload once from a seeded
`random.Random` and writes it as JSON: for each job the arrival time, SKU, routing as server indices, processing
times and due date. The shop follows `Scenario.pure_job_shop(n_servers=10)`:

- routing length uniform in 1..10, with distinct servers;
- truncated 2-Erlang processing times (rate 2, at most 4);
- Poisson arrivals at the rate of the target utilization;
- due date = arrival + Uniform(30, 45).

`benchmarks/feeder.py` builds the shop with constructors that both versions have: `Environment`, `ShopFloor`,
ten `Server(capacity=1)`, `PreShopPool` and `LumsCor` (norm 6, `check_timeout=5`, allowance factor 2, as the G1
reference). The `LumsCor` source is identical in both versions. `LumsCor` sets the PST rule on a router stub,
and the feeder gives that rule to every job, as `Router.generate_job` does. A process then waits until each
arrival and calls `PreShopPool.add(ProductionJob(...))`. No random number is drawn during the run. The run stops
1,000 time units after the last arrival.

For each run the feeder checks:

- every job and operation of the workload finished;
- the counts match the workload;
- in mode `none`, the bus has no subscriber.

It also fingerprints the trajectory: SKU, due date, pool exit and finish time of every finished job, in
completion order. `compare.py` refuses to compare results whose workload, counts or trajectory differ. The
trajectory fingerprint is identical between 0.12.0 and the branch, on CPython and on PyPy, for all four
workloads. Both versions therefore do the same work, event for event.

**Observers.** Each version runs with its defaults: the shop floor's `EMAMetricsCollector`, the per-environment
`SimLogger` at INFO and, on the branch, an event bus with no subscriber. The comparison therefore measures what a
user of each release pays without observing. The 0.12.0 side includes its `env.debug` calls (nine call sites on
this path: three in `Server`, four in `ShopFloor`, two in `PreShopPool`). §5.2 removes them to isolate SP1's own
cost.

**Timing.** One sample is the wall time of one run, measured with `time.perf_counter`. It covers creating the
environment, enabling the digest or the `TraceRecorder` (default chunk limits), building the shop, the run, and
`env.close()`, which flushes the trace. Loading the JSON is excluded, and `gc.collect()` runs before each run.
Settings:

- CI-size workloads: `--warmup 1` (CPython) or `--warmup 8` (PyPy), `--repeat 5`, `--processes 3`. Each of three
  fresh interpreters does the warm-up runs and five timed runs, and the 15 samples are pooled.
- Full-size workloads: one process, one warm-up run, three timed runs.

On PyPy the JIT settles after about seven runs of the CI workload (1.35 s on the first run, about 0.55 s from
the seventh). Fresh PyPy processes settle at speeds up to about 10 % apart (§3), which is why samples are pooled
across processes. The reported values are the median and the interquartile range (IQR) of the samples.

**Peak memory** is `ru_maxrss` of a separate process that loads the workload and runs it once. **Seeks** use
`Trace.open` on the trace of the last timed run, then 500 cold `state_at` calls (`--seeks 500`; every seek number
in this report uses 500, while the CI job uses `run.py`'s default of 200) at uniformly random times (seed
0). The reader's chunk cache is cleared before each call. This differs from G1, which seeked to random event
cursors, so the two reports' seek numbers are not directly comparable. **Sampling** (`bench_sampling.py`, T14) is
described in §5.6.

## 2. Environment

| Item | Value |
|---|---|
| Machine | eva: MacBook Pro 2021, Apple M1 Pro (10 cores), 16 GB, macOS 27.0.1, on AC power |
| CPython | 3.14.1 (uv build) |
| PyPy | 3.11.11, PyPy 7.3.18 (msgpack uses its pure-Python fallback) |
| Dependencies | simpy 4.1.2 in every environment, msgpack 1.2.3 on the branch; 0.12.0 and the branch installed in separate venvs, each version once per interpreter |
| Branch | 6088e6b (library code), installed non-editable |
| Load | Not an idle machine: other agent sessions were running (load average 2.5–3.7). One calibration pass ran during a macOS XProtect scan and was discarded (§3). |

## 3. Noise calibration

Each comparison runs 0.12.0 against itself: two `run.py` invocations with the CI settings, one after the other,
and the ratio of the second median to the first. There are ten comparisons per interpreter, on
`jobshop10-u90-5k`.

| Interpreter, settings | Ratios (10 comparisons) | Max ratio | Min ratio | Max deviation |
|---|---|---|---|---|
| CPython 3.14, 3 processes × 5 runs, warm-up 1 | 1.0046, 1.0026, 1.0060, 1.0058, 0.9980, 0.9978, 0.9983, 0.9918, 1.0006, 1.0049 | 1.0060 | 0.9918 | 0.8 % |
| PyPy 3.11, 3 processes × 5 runs, warm-up 8 | 1.0057, 1.0172, 0.9836, 0.9909, 1.0047, 0.9739, 0.9683, 0.9604, 1.0056, 0.9844 | 1.0172 | 0.9604 | 4.1 % (1/0.9604) |
| CPython 3.14, 1 process × 15 runs, warm-up 2 (first design) | 1.0035 … 0.9782 | 1.0061 | 0.9782 | 2.2 % |
| PyPy 3.11, 1 process × 15 runs, warm-up 8 (first design) | 1.0100 … 0.8910 | 1.0411 | 0.8910 | 12.2 % |

With a single process per side, PyPy's run-to-run variance between processes reached 12 %. Medians of single
processes fell around either 0.49 s or 0.55 s. Pooling three processes per side reduced the deviation to 4.1 %,
so the gate uses three processes per side. A CPython pass that coincided with an XProtect scan using 270 % CPU
gave a ratio of 0.924. It was discarded and rerun once the machine was quiet. The CI runners will not be quiet
either.

**Proposed band** (used in `ci.yml`): **2 % on CPython** (0.8 % observed with the CI settings, widened to cover
the 2.2 % of the single-process design and shared runners) and **5 % on PyPy** (4.1 % observed). The gate fails
mode `none` above 5 % (CPython) or 8 % (PyPy) median overhead. The band was calibrated on eva, not on GitHub
runners. If the first CI runs show base-to-base swings larger than the band, recalibrate it there with the same
procedure.

## 4. Workloads

| Workload | Jobs | Operations | Utilization | Seed | Max PSP length | Committed |
|---|---|---|---|---|---|---|
| `jobshop10-u90-5k` | 5,000 | 27,566 | 0.90 | 1 | 73 | yes (CI) |
| `jobshop10-u95-5k` | 5,000 | 27,485 | 0.95 (congested) | 2 | 100 | yes (CI) |
| `jobshop10-u90-50k` | 50,000 | 274,173 | 0.90 | 3 | 112 | no (4.8 MB, regenerated with CPython) |
| `jobshop10-u95-50k` | 50,000 | 275,700 | 0.95 (congested) | 4 | 200 | no |

The congested variant is LumsCor at utilization 0.95, as §14 specifies. LumsCor keeps shop queues bounded by its
norms, so the congestion shows up as a longer pre-shop pool, about twice as long as at 0.90. That pool is a list
field whose `insert`/`remove` deltas and snapshot grow with its length. SHA-256 of the full-size files:
`0b1c3e9a…` (u90) and `633dc315…` (u95). CPython 3.12 and 3.14 generate identical files.

## 5. Results

### 5.1 Mode `none` against `simulatte==0.12.0` (the gate)

Medians with the IQR in parentheses. Overhead = median(head) / median(0.12.0) − 1.

| Interpreter | Workload | 0.12.0 | Branch | Overhead | Limit (3 % + band) | Verdict |
|---|---|---|---|---|---|---|
| CPython 3.14 | u90-5k | 0.889 s (0.009) | 0.673 s (0.014) | −24.3 % | +5 % | pass |
| CPython 3.14 | u95-5k | 0.945 s (0.015) | 0.723 s (0.010) | −23.5 % | +5 % | pass |
| CPython 3.14 | u90-50k | 8.756 s (0.058) | 6.699 s (0.015) | −23.5 % | +5 % | pass |
| CPython 3.14 | u95-50k | 10.131 s (0.196) | 7.925 s (0.104) | −21.8 % | +5 % | pass |
| PyPy 3.11 | u90-5k | 0.561 s (0.036) | 0.351 s (0.018) | −37.4 % | +8 % | pass |
| PyPy 3.11 | u95-5k | 0.557 s (0.050) | 0.334 s (0.032) | −40.0 % | +8 % | pass |
| PyPy 3.11 | u90-50k | 5.613 s (0.194) | 3.077 s (0.042) | −45.2 % | +8 % | pass |
| PyPy 3.11 | u95-50k | 5.834 s (0.054) | 3.783 s (0.121) | −35.2 % | +8 % | pass |

For reference, G1 measured 7.25 s for 50k jobs with the router. The feeder's 6.70 s does not include sampling.

### 5.2 SP1's own cost on the unobserved path (diagnostic)

To separate SP1's additions from the removed logging, I made a copy of 0.12.0 (CPython and PyPy venvs) with every
`*.env.debug(...)` statement deleted from `psp.py`, `server.py`, `shopfloor.py` and `router.py`. The deletion
uses an AST pass that replaces each such expression statement with `pass` (12 statements). The pass is committed
as `benchmarks/strip_debug.py`, with usage in `benchmarks/README.md`. Its output is byte-identical to the copy
measured here. A rerun through it (`PYTHONPATH` copy, `--repeat 3`, one process) gave +10.0 % on CPython u90-5k.
The copy produces the same trajectory fingerprint as 0.12.0 and the branch.

| Interpreter | Workload | 0.12.0 without `env.debug` | Branch | Branch overhead |
|---|---|---|---|---|
| CPython 3.14 | u90-5k | 0.621 s (0.010) | 0.673 s (0.014) | **+8.4 %** |
| CPython 3.14 | u95-5k | 0.674 s (0.012) | 0.723 s (0.010) | **+7.2 %** |
| CPython 3.14 | u90-50k | 6.094 s (0.053) | 6.699 s (0.015) | **+9.9 %** |
| CPython 3.14 | u95-50k | 7.309 s (0.024) | 7.925 s (0.104) | **+8.4 %** |
| PyPy 3.11 | u90-5k | 0.438 s (0.016) | 0.351 s (0.018) | −19.8 % |
| PyPy 3.11 | u95-5k | 0.434 s (0.021) | 0.334 s (0.032) | −23.0 % |
| PyPy 3.11 | u90-50k | 3.880 s (0.029) | 3.077 s (0.042) | −20.7 % |
| PyPy 3.11 | u95-50k | 4.488 s (0.018) | 3.783 s (0.121) | −15.7 % |

Removing the debug calls makes 0.12.0 30 % faster on CPython (0.889 → 0.621 s) and 22 % faster on PyPy
(0.561 → 0.438 s).

**Where the CPython cost is.** I ran cProfile on one warmed run of `u90-5k` for the copy and for the branch and
compared total time per function. Under the profiler the total grows 2.325 → 2.611 s (+0.29 s; the profiler
inflates call-heavy code). The differences:

| Source | Calls | Profiled cost | Note |
|---|---|---|---|
| `Environment.wants` → `EventBus.wants` | 230,540 (46 per job, 8.4 per operation) | 0.126 s cumulative | Two Python calls and a dict lookup per guard, even though the bus has no subscriber. About 45 % of the difference. |
| `Server._trigger_put` additions | 55,132 | +0.040 s own time | Pending-arrival bookkeeping, job `location` strings, detection of the grant. |
| `Server._do_get` override | 27,566 | 0.086 s cumulative | Detects `job.released`. |
| `ShopFloor._operate` wrapper | 55,132 (generator) | 0.080 s cumulative | Extra generator frame around `Server.process_job` per operation. |
| `sort_queue` key function, extra `len`/`dict.get` | – | about +0.06 s | `_request_key` replaces the lambda, plus the `wants` check inside `sort_queue`. |
| `EntityRegistry.attach`/`retire` per job | 5,012 / 5,000 | 0.028 s cumulative | Small, partly offset by no longer calling `uuid4` (−0.016 s). |

None of these was changed in this task. If the controller wants SP1's own cost under 3 % on CPython, the
candidates are:

- a single attribute check instead of `env.wants` while the bus has no subscriber, for example a per-bus `idle`
  flag or the guards reading `bus._routes` directly;
- folding `_operate` back into `main`;
- merging the `_trigger_put`/`_do_get` bookkeeping.

### 5.3 Modes `digest` and `full` (branch only)

Ratio = median of the mode / median of the branch's `none` on the same workload and interpreter.

| Interpreter | Workload | `none` | `digest` | Ratio | `full` | Ratio |
|---|---|---|---|---|---|---|
| CPython 3.14 | u90-5k | 0.673 s | 1.904 s (0.027) | 2.83 | 2.973 s (0.043) | 4.42 |
| CPython 3.14 | u95-5k | 0.723 s | 2.032 s (0.093) | 2.81 | 3.114 s (0.049) | 4.31 |
| CPython 3.14 | u90-50k | 6.699 s | 19.229 s (0.139) | 2.87 | 29.421 s (0.121) | 4.39 |
| CPython 3.14 | u95-50k | 7.925 s | 20.374 s (0.071) | 2.57 | 31.901 s (0.433) | 4.03 |
| PyPy 3.11 | u90-5k | 0.351 s | 1.255 s (0.075) | 3.57 | 2.001 s (0.053) | 5.70 |
| PyPy 3.11 | u95-5k | 0.334 s | 1.288 s (0.040) | 3.86 | 2.000 s (0.050) | 5.99 |
| PyPy 3.11 | u90-50k | 3.077 s | 11.463 s (0.212) | 3.73 | 19.636 s (0.806) | 6.38 |
| PyPy 3.11 | u95-50k | 3.783 s | 12.367 s (0.070) | 3.27 | 22.892 s (1.101) | 6.05 |

The CPython ratios match G1's §3.1 (2.9× and 4.5× on the 4-server workload). In absolute time PyPy is faster in
every mode. Its ratios are higher because its `none` is much faster, while encoding (msgpack's pure-Python
fallback) does not speed up as much.

**Writer throughput.** In the 50k-job `full` run on CPython, the writer's 64 MiB backpressure bound engaged 225
times and held the simulation for 3.35 s in total. That is 11 % of the run: the writer thread, which does zlib
and snapshot encoding under the GIL, is the bottleneck of `full` on long runs.

### 5.4 Trace size, chunks and seek (C1.7)

Default chunk limits. Seeks: 500 cold seeks at uniformly random times (CI uses 200).

| Interpreter | Workload | Trace | Per job | Chunks | `Trace.open` | Seek p50 | Seek p95 | Seek max |
|---|---|---|---|---|---|---|---|---|
| CPython 3.14 | u90-5k | 7.61 MB | 1.52 KB | 34 | 0.8 ms | 43.9 ms | 51.0 ms | 70.9 ms |
| CPython 3.14 | u95-5k | 7.73 MB | 1.55 KB | 34 | 0.8 ms | 45.3 ms | 52.3 ms | 70.6 ms |
| CPython 3.14 | u90-50k | **75.15 MB** | 1.50 KB | 342 | 2.5 ms | 43.6 ms | **54.6 ms** | 396.7 ms |
| CPython 3.14 | u95-50k | 78.55 MB | 1.57 KB | 343 | 2.6 ms | 46.0 ms | 55.6 ms | 450.5 ms |
| PyPy 3.11 | u90-5k | 7.62 MB | 1.52 KB | 34 | 14.5 ms | 31.5 ms | 51.0 ms | 105.0 ms |
| PyPy 3.11 | u95-5k | 7.73 MB | 1.55 KB | 34 | 13.6 ms | 30.3 ms | 49.5 ms | 116.0 ms |
| PyPy 3.11 | u90-50k | 75.15 MB | 1.50 KB | 342 | 18.1 ms | 29.8 ms | **55.5 ms** | 78.7 ms |
| PyPy 3.11 | u95-50k | 78.55 MB | 1.57 KB | 343 | 26.0 ms | 32.4 ms | 58.6 ms | 158.9 ms |

Against C1.7 (10-server job shop over 50,000 jobs, on a 2021 laptop):

- `full` trace under 100 MB: **75.1 MB** at utilization 0.90 and 78.5 MB congested. Holds, with 25 % headroom.
- Seek p95 under 200 ms: **55 ms** at 0.90 and 56–59 ms congested, on both interpreters. Holds.
- Seek maximum: one or two seeks out of 500 reached 397–451 ms on CPython at 50k. Every percentile up to p95
  stays near 55 ms, so these look like pauses (GC or the OS) rather than a slow chunk. The target is set at p95.
- Recording does not grow memory without bound. On the 50k run, RSS rises to about 320 MB in the first 9 s
  while the writer queue fills to its bound. After that it grows at about 3 MB/s, which is the rate of the
  simulation's own state (finished jobs stay in `jobs_done`) at the throttled pace.

### 5.5 Peak memory

Peak RSS of a fresh process that loads the workload and runs it once. RSS before the run (interpreter, imports,
workload) is 40–42 MB on CPython and 66–69 MB on PyPy at 5k jobs, 80–82 MB and 92–94 MB at 50k.

| Interpreter | Workload | 0.12.0 `none` | Branch `none` | `digest` | `full` |
|---|---|---|---|---|---|
| CPython 3.14 | u90-5k | 48 MB | 51 MB | 51 MB | 120 MB |
| CPython 3.14 | u90-50k | 161 MB | 173 MB | 173 MB | 409 MB |
| CPython 3.14 | u95-50k | 158 MB | 173 MB | 173 MB | 392 MB |
| PyPy 3.11 | u90-5k | 99 MB | 104 MB | 125 MB | 142 MB |
| PyPy 3.11 | u90-50k | 254 MB | 255 MB | 335 MB | 358 MB |
| PyPy 3.11 | u95-50k | 254 MB | 262 MB | 339 MB | 345 MB |

On CPython the branch's `none` uses 12–15 MB more at 50k jobs. The likely cause is the entity registry's
per-job bookkeeping and the ids that remain referenced by `jobs_done`. `full` adds about 236 MB on CPython at 50k:
the 64 MiB pending-bytes bound plus the encoded chunks and decoded objects waiting for the writer.

### 5.6 Sampling cost (T14)

`bench_sampling.py --jobs 50000` builds a `Scenario.pure_job_shop(n_servers=10, target_utilization=0.9)` floor
and router with the builder's default distributions. The router feeds a sink that only counts jobs, so no job
is processed. On the branch the samplers are bound to named streams; 0.12.0 calls `random`. Sampler calls per job
are the inter-arrival time, the SKU, the routing, one processing time per operation and the due-date offset:
about 9.5 per job. The run includes job construction and the router's timeouts. 10 timed runs after warm-up 1
(CPython) or 8 (PyPy).

| Interpreter | Version | Median (IQR) | Jobs/s | Sampler calls/s | Ratio to 0.12.0 |
|---|---|---|---|---|---|
| CPython 3.14 | 0.12.0 | 0.648 s (0.016) | 77,100 | 733,000 | – |
| CPython 3.14 | 0.12.0 without `env.debug` | 0.529 s (0.018) | 94,400 | 898,000 | 0.82 |
| CPython 3.14 | branch | 0.466 s (0.016) | 107,400 | 1,022,000 | **0.72** |
| PyPy 3.11 | 0.12.0 | 1.016 s (0.015) | 49,200 | 468,000 | – |
| PyPy 3.11 | 0.12.0 without `env.debug` | 0.979 s (0.038) | 51,100 | 485,000 | 0.96 |
| PyPy 3.11 | branch | 0.204 s (0.004) | 245,000 | 2,331,000 | **0.20** |

Sampling from bound per-stream samplers costs less than 0.12.0's sampling, even when 0.12.0's debug calls are
removed (−12 % on CPython). On PyPy it is five times faster: 0.12.0's samplers go through the module-level
`random` functions and `Distribution.__call__` on every draw, which PyPy handles poorly in this loop.

## 6. Verdict

| Criterion | Result |
|---|---|
| No-subscriber overhead ≤ 3 % + band against 0.12.0 (C1.9, G3) | **Pass** on CPython (−22 to −24 %) and PyPy (−35 to −45 %), at CI and full size, normal and congested. |
| SP1's own unobserved-path cost (diagnostic, not the gate) | +7 to +10 % on CPython, −16 to −23 % on PyPy (§5.2). The gate's margin depends on the removed debug logging. |
| `full` trace < 100 MB for 10 servers × 50,000 jobs (C1.7) | **Holds**: 75.1 MB (78.5 MB congested). |
| Seek p95 < 200 ms on a 2021 laptop (C1.7) | **Holds**: 54.6 ms CPython, 55.5 ms PyPy (≤ 58.6 ms congested). |
| Recording without unbounded memory (C1.7) | **Holds**: bounded by the 64 MiB writer bound after the first seconds (§5.4). |

## 7. Proposed budgets

C1.9 leaves `digest`/`kpi`, `full` and sampling to SP1. These are regression budgets with about 10 % headroom
over the measured worst case, on the same workloads. The digest is the main cost of the `kpi` mode that G4
builds, so the `digest` budget is the proposed starting point for `kpi`.

| Mode | Measure | Proposed budget | Measured (worst of four workloads) |
|---|---|---|---|
| `digest` | median / branch `none` | ≤ 3.2× CPython, ≤ 4.3× PyPy | 2.87× / 3.86× |
| `full` | median / branch `none` | ≤ 4.9× CPython, ≤ 7.0× PyPy | 4.42× / 6.38× |
| `full` | trace size per job, 10-server job shop | ≤ 1.75 KB/job (87.5 MB at 50k jobs) | 1.57 KB/job |
| `full` | cold seek p95 at 50k jobs, eva-class laptop | ≤ 100 ms | 58.6 ms |
| `full` | extra peak RSS over `none` | ≤ 256 MB with default chunk limits and writer bound | 236 MB |
| Sampling | router-only median / 0.12.0 | ≤ 1.03 + band (no regression against 0.12.0) | 0.72 CPython / 0.20 PyPy |

The `full` ratio is bounded by the writer (§5.3). A faster writer, or a smaller pending bound with chunk
encoding moved off the simulation thread, would bring the ratio down before G4 tightens it. CI reports these
measures in the step summary but does not gate them. Gating them needs the same calibration on the runners.

## 8. CI job

Job `bench` in `.github/workflows/ci.yml`, a matrix over CPython 3.14 and PyPy 3.11 on `ubuntu-latest`:

1. Installs the branch in `/tmp/head`, freezes its dependencies as constraints, and installs
   `simulatte==0.12.0` in `/tmp/base` with them. Both versions run on the same versions of their shared dependencies
   (simpy and the rest).
2. Gate step: mode `none` on `jobshop10-u90-5k` and `jobshop10-u95-5k` in both environments with the settings
   of §1 (`--repeat 5 --processes 3`, warm-up 1 or 8). `compare.py --budget 0.03 --noise 0.02|0.05` appends a
   table to `$GITHUB_STEP_SUMMARY` and fails the job above the limit.
3. Reporting step (runs even when the gate fails): `digest` and `full` against the branch's `none` (200 seeks),
   the sampling benchmark (20,000 jobs) against 0.12.0, and two tables from `summarize.py`. The details table
   has peak RSS, trace size, chunks, seek p50/p95, jobs/s and draws/s. The provenance table has, per version,
   the commit, logging level and hardware (machine, CPU model, CPU count), the fields C1.9 asks to record. The
   result JSON files, which carry the same fields, are uploaded as artifacts.

I checked the step scripts by running them on eva against fresh `/tmp/ci-*` environments, with `--repeat 1
--processes 2` and Linux-only steps skipped. Both the summary output and the gate's exit status behaved as
intended: exit 1 above the limit, 2 for incomparable results.

The CI duration is estimated from eva's timings, scaled for slower runners. I have not observed it, since
nothing was pushed: about 6 minutes on CPython (1.5 min gate, 3 min reporting, setup) and about 9 minutes on
PyPy. `timeout-minutes` is 25.

## 9. Findings and follow-ups (not changed in this task)

- **CPython and PyPy digests differ even without random draws.** On `u90-5k` the trajectories are identical,
  yet the digests differ (`482d8c1c…` on CPython, `b2357072…` on PyPy). The first differing event is
  `job.finished` of `job-13`: `total_queue_time` is 3.2104609999999987 on CPython and 3.2104609999999982 on PyPy.
  `BaseJob.total_queue_time` is a `sum()` of floats, and CPython 3.12+ sums floats with compensated (Neumaier)
  summation while PyPy 3.11 (and CPython 3.11) do not. The G1 report attributed the cross-interpreter digest
  difference to RNG derived methods. This is a second, RNG-free source, and it probably also separates CPython
  3.11 from 3.12+, which share `requires-python`. Fix options: `math.fsum` or an explicit loop for semantic float
  sums, or document the interpreter-version dependence in C1.6.
- **LumsCor releases are recorded as removals.** `LumsCor.periodic_release` and `starvation_release` call
  `psp.remove(job=job)` without `reason`. The trace therefore records `psp.exited` with `reason="removed"` and
  location null for jobs that LumsCor releases, instead of `"released"`/`"postponed"` and `"transit"` (spec
  §5.2). This is presumably covered by the G4 migration of the policies.
- The branch's `none` mode keeps 12–15 MB more at 50k jobs on CPython (§5.5).
- PyPy's `Trace.open` takes 14–26 ms against 1–3 ms on CPython (msgpack's pure-Python fallback decoding the head).
- Infrastructure: `benchmarks/` is excluded from the sdist (`pyproject.toml`); ruff and ty pass on it (the
  pre-commit hooks cover it), and the test suite does not import it.
