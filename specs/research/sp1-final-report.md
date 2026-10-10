# SP1 final report: events, traces and KPIs (0.13)

Branch `feature/sp1-events-trace`, Task 25. Library code at 486630e; benchmark code at 96d4f9a (the benchmark scripts were uncommitted when the measurements were taken, so the `commit` field of the result files reads 486630e). Spec: `specs/2026-10-08-sp1-events-trace-design.md` (rev 4); budgets from the global design §C1.9. Measured on eva, 2026-10-09.

**Verdict.** All final checks pass: the full suite with the 99 % branch-coverage gate, the PyPy lane, ruff, ty, the TypeScript conformance tests and the docs build. The unobserved path of the branch is 26 to 29 % faster than `simulatte==0.12.0` on CPython 3.14 and 33 to 38 % faster on PyPy 3.11, and 4.4 to 5.0 % slower than 0.12.0 without its debug calls on CPython (limit 8.5 %). The new modes cost, against the branch's `none` on the same workload: `default` (the default EMA collector) +6 to +8 % on CPython and +3 % on PyPy; `default_logging` nothing measurable; `kpi` 3.0 to 3.4× on CPython and 3.7 to 4.1× on PyPy. Of the D57 regression metrics, all hold except one: the CPython `full` ratio on the 50,000-job workload at utilization 0.90 is 4.83 to 4.99× against a budget of 4.9× (§7.1). Budgets for `default`, `default_logging` and `kpi` were proposed in §6 and **accepted by Davide on 2026-10-10 (D59)**, report-only until calibrated on CI runners like D57; the global spec C1.9 table now lists them. D57's CPython `full` budget was first kept at 4.9× (D59), then raised to 5.3× (D60) after the adversarial-review fixes added the `server.work_credited` event.

## 1. What SP1 delivered

SP1 shipped in four gates (spec §2). The per-gate reports are `sp1-g1-report.md` and `sp1-g3-report.md`; G2 had no report (its acceptance is the TypeScript test suite).

| Gate | Delivered | Outcome |
|---|---|---|
| G1. Vertical slice | Entities with stable ids, the typed event bus and catalog, state deltas, the semantic digest, activation and the prelude, seeded RNG streams, the trace writer and the Python reader, for `Server`, `ProductionJob`, `ShopFloor`, `PreShopPool` and `Router`; the `queue_length` fix | Accepted. Seek equals replay at every chunk boundary and at sampled cursors; the digest is identical across observer configurations and `PYTHONHASHSEED` values. A fix wave after the whole-branch review brought digest mode from 4.6× to 2.9× and `full` from 7.3× to 4.5× the unobserved time. |
| G2. TypeScript conformance | `studio/` workspace with `@simulatte/trace`, decoding the G1 fixtures and replaying state | Accepted. TS replay state equals Python replay state on all fixtures, including hostile map keys and default-generated seeds (57 conformance tests). |
| G3. Budgets (slice) | Benchmarks of §14 on the slice, the two-baseline CI gate (released 0.12.0 and 0.12.0 without its `env.debug` calls) | Accepted with decisions D55 (two baselines), D56 (tuning of the unobserved path), D57 (regression budgets for `digest` and `full`) and D58 (`math.fsum` for semantic sums). |
| G4. Migration | The remaining components publish events (policies, fleet, orders, AGVs, traffic, warehouses, charging, parking), the logging rebuild (log sinks on the bus), KPI collectors and `Collector`/`KPI` declarations, the intralogistics collectors, the observer-invariance and determinism suite, the top-level exports, documentation, examples and the 0.13 changelog with the migration notes | Complete; this report is the final benchmark step (Task 25). |

Between the gates, the unobserved-path tuning of Task 12b cut SP1's own CPython cost from +7 to +9 % to +4 to +5 % against the stripped baseline (G3 report §9), and Task 12c made the semantic float sums `math.fsum`, which also made the CPython and PyPy digests of the reference shop equal.

## 2. Verification

All commands ran from the repository root at 486630e with `MPLBACKEND=Agg`.

| Check | Command | Result |
|---|---|---|
| Full suite with coverage | `uv run pytest` | 1375 passed in 44.25 s; "Required test coverage of 99% reached. Total coverage: 99.69%" (6,743 statements, 9 missed; 1,884 branches, 18 partial) |
| PyPy lane | `MPLBACKEND=Agg uv run --python pypy-3.11 pytest tests/core tests/intralogistics --no-cov -q -p no:cacheprovider`, then `uv sync --dev` | 1343 passed, 1 skipped in 75.59 s |
| Lint | `uv run ruff check src tests` | All checks passed |
| Format | `uv run ruff format --check src tests benchmarks` | 163 files already formatted (164 after the new benchmark script) |
| Types | `uv run ty check src` | All checks passed |
| TypeScript | `corepack pnpm -C studio test` (Node v24.13.0) | `@simulatte/trace`: 1 file, 57 tests passed |
| Docs | `uv run zensical build` | "No issues found" |

The CPython lanes of the CI matrix (3.12, 3.13, 3.14) and the 3.15 pre-release were not run locally; CI runs them. Nothing has been pushed.

## 3. Benchmark method

The method is that of the G3 report (§1, same workloads, same feeder, same statistics). What is new:

- **New modes** (`benchmarks/feeder.py`, `run.py`): `default_logging`, `bare` and `kpi`. Definitions below.
- **Intralogistics workload** (`benchmarks/intralogistics.py`, ruling R28): the advanced intralogistics example scaled up, branch only.
- **Interleaving.** For the CI-size workloads, each interpreter ran two rounds over all 26 configurations (3 versions × modes × 2 workloads), in an order rotated between the rounds. Each configuration used the CI settings (`--repeat 5 --processes 3`, warm-up 1 on CPython and 8 on PyPy), so a pooled median has 30 samples. Medians and IQRs below are pooled over both rounds; "spread" is the difference between the two rounds' medians of the same configuration. The 50,000-job workloads and the intralogistics workload ran one round (50k: 1 process, 3 timed runs after warm-up 1 on CPython or 2 on PyPy; intralogistics: 2 processes × 3 timed runs after warm-up 1 or 2).
- **Discarded round.** A first pair of CPython rounds was discarded because a stray probe process of mine (an orphaned loop of a calibration script) ran concurrently with them. Every number in this report comes from runs after it was killed and the machine was checked to hold no other benchmark process.

Environment: eva (MacBook Pro 2021, Apple M1 Pro, 10 cores, macOS 27.0.1), CPython 3.14.1, PyPy 3.11.11 (7.3.18), simpy 4.1.2 and msgpack 1.2.3 in every environment, 0.12.0 installed with the branch's dependency versions as constraints. The machine was not idle: the desktop session (a browser, audio and window services) kept the load average at 2.3 to 3.7 throughout. Absolute times are therefore about 10 % slower than in the G3 report (branch `none` on `u90-5k`: 0.749 s against 0.673 s); only ratios inside one interleaved run are compared. The full-size workload files are regenerated with CPython and have the SHA-256 prefixes of the G3 report (`0b1c3e9a…`, `633dc315…`).

**Mode definitions.** Every mode runs with the default log sinks of its version (`Environment()`'s on the branch, `SimLogger` at INFO in 0.12.0).

| Mode | Shop-floor metrics | Subscribers on the branch | Versions |
|---|---|---|---|
| `none` | off | none | all (gated) |
| `default` | each version's default (`EMAMetricsCollector` in 0.12.0; `EMACollector` on the bus) | the `EMACollector` | all |
| `default_logging` | off | none; the feeder checks that the default logging is active | all |
| `bare` | off | none, and the default log sinks closed | branch only (diagnostic) |
| `kpi` | the default `EMACollector` | `TraceRecorder(level="kpi")` (which enables the digest), the `EMACollector` and a `ShopFloorKPIs` collector | branch only |
| `digest`, `full` | off | the digest, or `TraceRecorder` at default limits | branch only |

Two choices need justification.

- **`default_logging` is the same configuration as `none`.** The default sinks attach in `Environment.__init__`, so every mode already has them, and they subscribe to `log` events only: no domain event is built for them, and nothing on the shop path emits a `log` event at INFO. The mode therefore cannot differ from `none` by construction; it exists to give the budget a name and to check (before each run) that the default logging is really active. To measure what the default logging costs at all, I added `bare`, the same shop with the default sinks closed before the shop is built. The logging cost is `default_logging` divided by `bare`. The costly logging, DEBUG level with domain events rendered to text, is not a default and was not measured.
- **`kpi` is the KPI recorder plus both default collectors.** `TraceRecorder(level="kpi")` alone would add only the digest and an empty KPI series, since no collector would produce `kpi.sample` records. I added the default `EMACollector` (as the controller's brief allowed) and a `ShopFloorKPIs` collector, the window-aware collector that a KPI trace is meant for. `kpi` minus `digest` is then the share of the collectors, the recorder and the EMA together. The 0.12.0 side has no equivalent, so the mode is a ratio against the branch's `none`, like `digest` and `full`.

## 4. Results at CI size

Medians (IQR) pooled over two rounds. Ratio columns divide the branch's median by the median of the named run.

### 4.1 Mode `none` (the gate, reproduced)

| Interpreter | Workload | 0.12.0 | 0.12.0 without debug | Branch | vs released | Limit | vs stripped | Limit |
|---|---|---|---|---|---|---|---|---|
| CPython 3.14 | u90-5k | 1.058 s (0.040) | 0.717 s (0.016) | 0.749 s (0.014) | −29.2 % | +5 % | +4.4 % | +8.5 % |
| CPython 3.14 | u95-5k | 1.099 s (0.017) | 0.772 s (0.013) | 0.810 s (0.025) | −26.2 % | +5 % | +5.0 % | +8.5 % |
| PyPy 3.11 | u90-5k | 0.482 s (0.014) | 0.383 s (0.020) | 0.301 s (0.013) | −37.6 % | +8 % | −21.5 % | +8 % |
| PyPy 3.11 | u95-5k | 0.491 s (0.012) | 0.407 s (0.021) | 0.328 s (0.015) | −33.1 % | +8 % | −19.3 % | +8 % |

Both gates pass with the margins of the G3 report. Round-to-round spread of the branch's `none` is 0.3 to 0.4 % on CPython and 2.5 to 3.2 % on PyPy; of 0.12.0, up to 3.9 % (CPython, `u90-5k`) and 0.2 % (PyPy).

### 4.2 Modes `default` and `default_logging`, against both baselines and the branch's `none`

| Interpreter | Workload | Mode | Branch | vs released | vs stripped | vs branch `none` | vs `bare` |
|---|---|---|---|---|---|---|---|
| CPython 3.14 | u90-5k | `default` | 0.806 s (0.014) | −25.6 % | +7.2 % | 1.076 | 1.059 |
| CPython 3.14 | u95-5k | `default` | 0.862 s (0.023) | −23.9 % | +8.6 % | 1.064 | 1.052 |
| PyPy 3.11 | u90-5k | `default` | 0.310 s (0.006) | −37.6 % | −23.0 % | 1.029 | 1.061 |
| PyPy 3.11 | u95-5k | `default` | 0.338 s (0.009) | −34.0 % | −20.9 % | 1.028 | 1.032 |
| CPython 3.14 | u90-5k | `default_logging` | 0.752 s (0.013) | −28.2 % | +5.0 % | 1.004 | 0.988 |
| CPython 3.14 | u95-5k | `default_logging` | 0.810 s (0.013) | −25.9 % | +5.1 % | 1.000 | 0.988 |
| PyPy 3.11 | u90-5k | `default_logging` | 0.295 s (0.007) | −38.4 % | −21.5 % | 0.982 | 1.012 |
| PyPy 3.11 | u95-5k | `default_logging` | 0.328 s (0.008) | −33.9 % | −18.5 % | 0.997 | 1.001 |

`bare` itself is 0.761 s and 0.820 s on CPython and 0.292 s and 0.327 s on PyPy, within 3 % of `none` in either direction (+1.6 % and +1.2 % on CPython, −2.9 % and −0.4 % on PyPy). The default logging has no measurable cost: every `default_logging / bare` ratio is within ±1.2 %, inside the noise of the interleaved runs (spread 0.7 to 1.8 % on CPython, 0.4 to 1.9 % on PyPy).

The cost of `default` is the default `EMACollector` on the bus: `job.finished` is built at each completion and the collector updates six averages. On CPython it is +6.4 to +7.6 % of the branch's `none`; the 0.12.0 collector costs less (stripped `default` / stripped `none` = 1.047 and 1.029), so the branch's default shop is 7.2 to 8.6 % slower than the stripped 0.12.0 default, against +4.4 to +5.0 % for `none`. That is about 3 points more than `none`, in line with the roughly 4 points the ledger estimated at Task 21 (Ruling R26).

### 4.3 Modes `kpi`, `digest` and `full`, against the branch's `none`

| Interpreter | Workload | `digest` | Ratio | `kpi` | Ratio | `full` | Ratio |
|---|---|---|---|---|---|---|---|
| CPython 3.14 | u90-5k | 2.260 s (0.024) | **3.02** | 2.533 s (0.036) | **3.38** | 3.527 s (0.065) | **4.71** |
| CPython 3.14 | u95-5k | 2.363 s (0.079) | **2.92** | 2.583 s (0.037) | **3.19** | 3.608 s (0.094) | **4.45** |
| PyPy 3.11 | u90-5k | 1.103 s (0.026) | **3.67** | 1.196 s (0.040) | **3.98** | 1.946 s (0.075) | **6.47** |
| PyPy 3.11 | u95-5k | 1.146 s (0.090) | **3.49** | 1.212 s (0.042) | **3.69** | 1.916 s (0.074) | **5.83** |

Round-to-round spread: 0.3 to 3.3 % on CPython and 1.4 to 8.0 % on PyPy (the PyPy `digest` on `u95-5k` is the 8.0 %).

`kpi` is `digest` plus 0.27 to 0.36 × the `none` time on CPython and 0.20 to 0.31 × on PyPy. Of that, the default `EMACollector` is 0.06 to 0.08 × (CPython) and 0.03 × (PyPy); the rest is the `ShopFloorKPIs` collector and the `kpi`-level recorder (which subscribes to `kpi.sample` only and writes 8 KB for 5,000 jobs).

## 5. Results at full size and on the intralogistics workload

### 5.1 The 50,000-job workloads (one process, 3 timed runs)

| Interpreter | Workload | 0.12.0 `none` | Branch `none` | `default` | `kpi` | `digest` | `full` |
|---|---|---|---|---|---|---|---|
| CPython 3.14 | u90-50k | 8.848 s | 6.064 s | 6.468 s (1.067) | 20.557 s (3.39) | 18.416 s (3.04) | 30.284 s (**4.99**) |
| CPython 3.14 | u95-50k | 9.899 s | 7.392 s | 7.926 s (1.072) | 22.229 s (3.01) | 19.897 s (2.69) | 31.753 s (4.30) |
| PyPy 3.11 | u90-50k | 5.321 s | 2.942 s | 3.043 s (1.034) | 11.931 s (4.06) | 11.106 s (3.78) | 19.763 s (6.72) |
| PyPy 3.11 | u95-50k | 5.095 s | 3.453 s | 3.610 s (1.046) | 12.993 s (3.76) | 12.127 s (3.51) | 19.717 s (5.71) |

Ratios against the branch's `none` in parentheses. The branch's `none` is 25 to 32 % faster than 0.12.0 on CPython and 32 to 45 % faster on PyPy at this size.

### 5.2 Trace size, seeks and memory (C1.7 and D57)

| Interpreter | Workload | `full` trace | Per job | Chunks | `Trace.open` | Seek p50 / p95 / max (500 cold seeks) | Peak RSS `none` → `full` (extra) |
|---|---|---|---|---|---|---|---|
| CPython 3.14 | u90-50k | 75.77 MB | 1.515 KB | 346 | 6.6 ms | 44.6 / 53.4 / 429.8 ms | 169 → 404 MB (+235 MB) |
| CPython 3.14 | u95-50k | 79.22 MB | 1.584 KB | 348 | 6.4 ms | 44.7 / 53.4 / 382.4 ms | 169 → 401 MB (+232 MB) |
| PyPy 3.11 | u90-50k | 75.77 MB | 1.515 KB | 346 | 19.6 ms | 29.1 / 51.9 / 93.9 ms | 259 → 342 MB (+83 MB) |
| PyPy 3.11 | u95-50k | 79.22 MB | 1.584 KB | 348 | 18.9 ms | 30.6 / 52.2 / 182.0 ms | 266 → 344 MB (+78 MB) |

Extra RSS is relative to the branch's `none` on the same workload. The `kpi` trace is 8.1 to 8.2 KB at 50,000 jobs, and the digest and `kpi` modes add no measurable memory on CPython (+0 to +2 MB) and +68 to +91 MB on PyPy. The 0.12.0 `none` run peaks at 161 MB (CPython) and 239 to 243 MB (PyPy).

### 5.3 Intralogistics (branch only, ratios against the branch's own `none`)

`benchmarks/intralogistics.py` builds the layout, SKUs, warehouses, AGV type, strategies and reorder-point replenishment of `examples/intralogistics_advanced.py` (16 nodes, 3 warehouses) and scales it up to 20 AGVs, an outbound order every 30 to 60 time units and 20 shifts of 28,800 time units (horizon 576,000). A run creates 22,000 orders, of which 21,995 are completed. It is seeded (`Environment(seed=42)`), so every mode does the same work and `compare.py` checks the order count, statuses and a fingerprint of the order trajectory. No 0.12.0 side exists (its intralogistics API differs). In `none` the fleet coordinator has no `OrderEMACollector`; `default` keeps it; `kpi` adds `FleetKPIs` and a `TraceRecorder(level="kpi")`.

| Interpreter | `none` | `default` | `default_logging` | `bare` | `kpi` | `digest` | `full` |
|---|---|---|---|---|---|---|---|
| CPython 3.14 | 6.887 s (0.095) | 7.484 s, 1.087 | 6.853 s, 0.995 | 6.958 s, 1.010 | 15.352 s, 2.23 | 14.327 s, 2.08 | 18.822 s, 2.73 |
| PyPy 3.11 | 3.078 s (0.029) | 3.128 s, 1.016 | 3.090 s, 1.004 | 3.089 s, 1.004 | 7.909 s, 2.57 | 7.402 s, 2.41 | 13.473 s, 4.38 |

The two processes of each cell agree within 2 %. The `full` trace is 33.1 MB (1.5 KB per order) and peaks at +13 MB (CPython) or +32 MB (PyPy) of RSS over `none`; `kpi` is 18.7 KB. The recording ratios are lower than on the job shop, probably because the fleet spends more simulation time per emitted event (path search, traffic checks); I did not profile it.

## 6. Budgets for `default`, `default_logging` and `kpi` (accepted, D59)

These follow the rule of the G3 report §7: the worst measured value plus about 10 % headroom, with the CI noise bands (2 % on CPython, 5 % on PyPy) added for the cross-version comparisons. Davide accepted them as proposed on 2026-10-10 (D59). They are in the global spec C1.9 table; CI reports them without gating until the bands are calibrated on runners, as for D57.

| Mode | Measure | Accepted budget | Measured (worst of the CI-size and 50k workloads) |
|---|---|---|---|
| `default` | median / released 0.12.0 `default` | ≤ 3 % + band (CPython 5 %, PyPy 8 %), as for `none` | −23.9 % CPython, −34.0 % PyPy |
| `default` | median / 0.12.0 `default` without debug calls | CPython ≤ 10 % + 2 % band; PyPy ≤ 3 % + 5 % band | +8.6 % CPython, −20.9 % PyPy |
| `default` | median / branch `none` (the default collector's own cost) | ≤ 1.10 CPython, ≤ 1.06 PyPy | 1.076 CPython, 1.046 PyPy (50k) |
| `default_logging` | median / branch `bare` | ≤ 3 % + band (CPython 5 %, PyPy 8 %); this is the 5 % target of C1.9; nothing on this shop logs at INFO, so it only guards the bus-subscription bookkeeping | 0.988 to 1.012 (noise) |
| `default_logging` | median / 0.12.0, released and stripped | the `none` gate's limits, unchanged (same configuration) | −25.9 % / +5.1 % CPython, −33.9 % / −18.5 % PyPy |
| `kpi` | median / branch `none` | ≤ 3.7× CPython, ≤ 4.5× PyPy | 3.39× CPython, 4.06× PyPy |

Reasoning.

- **`default`.** The cross-version budgets keep the form of the `none` gate. The stripped CPython budget is 10 % because the measured worst case is +8.6 % (`u95-5k`, pooled over two rounds, spread 1.6 %), and 6.5 % would fail it; the extra 3.5 points over the `none` budget are the default `EMACollector`'s event construction. If Davide prefers a tighter number, the ratio against the branch's `none` isolates the collector alone and does not depend on 0.12.0; 1.10 is 2.4 points above the worst CI-size ratio (1.076), and PyPy's 1.06 is 1.4 points above the worst 50k ratio (1.046; the CI-size PyPy ratios are 1.028 to 1.029).
- **`default_logging`.** The measured cost is zero within noise, so the budget is set by the 5 % target of C1.9, spelled as 3 % plus the noise band as in the other gates (5 % on CPython, 8 % on PyPy). It is judged against `bare`, not `none`, because the two latter configurations are the same. A budget against 0.12.0 adds nothing to the `none` gate. A caveat: on this workload nothing logs at INFO, so the budget only guards the bookkeeping of the bus subscriptions. It says nothing about scripts that log heavily, nor about DEBUG logging; measuring logging cost needs a workload that logs at INFO (follow-up, §8).
- **`kpi`.** Worst ratios are 3.38 and 3.39 on CPython and 3.98 and 4.06 on PyPy; +10 % gives 3.7 and 4.5. It is a ratio budget in the style of D57's `digest` and `full`, and like them it moves when the denominator moves: the 12b tuning made `none` about 10 % faster since G3 without changing the recording cost. The KPI collectors' share (C1.9: "fixed at the end of SP1") is `kpi` minus `digest`: at most 0.36 × CPython and 0.31 × PyPy of `none` with the default EMA included. The share is bounded by the `kpi` and `digest` budgets together, so I do not propose a separate budget for it.
- **Intralogistics ratios: no budget (decision D59: the workload stays report-only).** The numbers come from a single round of two processes, and there is no cross-version anchor, so a threshold would rest on little. Provisional figures, not adopted: `default` ≤ 1.15 CPython (1.087 measured); `kpi` ≤ 2.5× / 2.9×; `digest` ≤ 2.3× / 2.7×; `full` ≤ 3.0× / 4.8× (CPython / PyPy; measured 2.23 / 2.57, 2.08 / 2.41, 2.73 / 4.38). They should be re-measured on the CI runners before use.

The existing D57 budgets, checked against this run:

| Measure | D57 budget | CI-size, worst | 50k, worst | Verdict |
|---|---|---|---|---|
| `digest` / `none`, CPython | ≤ 3.2× | 3.02 | 3.04 | holds |
| `digest` / `none`, PyPy | ≤ 4.3× | 3.67 | 3.78 | holds |
| `full` / `none`, CPython | ≤ 4.9× | 4.71 | **4.99** (u90-50k) | borderline, see §7.1; kept at 4.9× (D59), **raised to 5.3× (D60)** |
| `full` / `none`, PyPy | ≤ 7.0× | 6.47 | 6.72 | holds |
| trace per job | ≤ 1.75 KB | – | 1.515 to 1.584 KB | holds |
| cold seek p95 at 50k jobs | ≤ 100 ms | – | 51.9 to 53.4 ms | holds |
| extra peak RSS over `none` | ≤ 256 MB | – | +235 MB (CPython), +83 MB (PyPy) | holds |

## 7. Findings

### 7.1 CPython `full` at 50,000 jobs is at the D57 limit

The first run gave 4.99× (30.284 s over 6.064 s) for `u90-50k`. I repeated the pair twice, alternating `none` and `full` in one process each: 29.606 s over 6.132 s (4.83×) and 29.798 s over 6.037 s (4.94×). The `full` time itself matches the G3 report (29.4 s); the ratio grew because the Task 12b tuning made `none` about 10 % faster (6.70 s then, about 6.1 s now). CI does not see it: CI runs `u90-5k` (4.71×) and gates nothing in this mode. The options considered were to accept a looser CPython `full` ratio (about 5.4× keeps the 10 % rule), to speed up the writer (G3 §5.3 measured the 64 MiB backpressure holding the simulation for 11 % of a 50k run), or to express the budget in absolute time. Davide first decided on 2026-10-10 to keep D57's 4.9× (D59). After the adversarial-review fixes added a `server.work_credited` event per operation (about 5 % more `full` recording time, so an expected 5.1 to 5.2× on `u90-50k`), he raised the CPython budget to 5.3× the same day (D60).

### 7.2 A saturated fleet is orders of magnitude slower

While choosing the scale of the intralogistics workload, a run with 20 AGVs and an outbound order every 15 to 30 time units for 10 shifts (`--agvs 20 --shifts 10 --interval-min 15 --interval-max 30`) did not finish `none` mode in more than nine minutes of CPU, while 30 to 60 time units finished 22,000 orders in 6.9 s (and a half-size run, 11,000 orders, in 3.4 s). The cause is the pending-order scan described in §8 (identified in the review of this task, with the timings listed there); no source was changed. This is not a regression claim: I have no 0.12.0 number. It is worth a look before anyone benchmarks an overloaded fleet. The scaled-up workload in `benchmarks/intralogistics.py` stays on the stable side of that threshold (the fleet completes all but 5 orders, which are in flight at the horizon).

### 7.3 Smaller notes

- `bare` is not measurably faster than `none` (+1.2 to +1.6 % on CPython, −2.9 to −0.4 % on PyPy, all within noise), so closing the default sinks buys nothing: the sinks add no subscriber to any domain event.
- On CPython, 0.12.0's default collector adds 2.9 to 4.7 % to its own `none`, the branch's `EMACollector` 6.4 to 7.6 %. Moving the collector onto the bus therefore costs 2.8 to 3.6 points more (the stripped comparison goes from +4.4 % to +7.2 % on `u90-5k` and from +5.0 % to +8.6 % on `u95-5k`), a little under the 4 points estimated at Task 21.
- The CI step summary rows for the new modes are reported only; the estimated durations in `ci.yml` (about 15 minutes on CPython and 20 on PyPy) are not observed on a runner.

## 8. Known limitations and follow-ups

From the ledger, grouped; none blocks the release.

**Pre-existing bugs, not fixed in SP1** (ruling R20, repro scripts were in `/tmp/sim-probe/`): an interrupted recharge or swap leaves its slot request queued and later granted; an inline `enter_node` (with `deadlock_timeout=None`) swallows a mission interrupt; a second `Interrupt` while `_run_mission`'s handler yields escapes `env.run`; `ParkingArea.enter` for an already-parked AGV leaks the previous slot request; `ReturnToOrigin` leaves an order PENDING without re-queueing; cancelling a COMPLETED order that is still repositioning flips it to CANCELLED; the idle-hook dispatch can race the old mission's `_agv_mission` pop.

**Design choices to confirm or revisit:**
- A run stopped by `run(until=<simpy.Event>)` records an incomplete manifest (R11).
- Non-wire priority values are recorded as `None` in `JobQueued` (R14).
- `env.wants` is a bound lookup set at construction; subclasses cannot override it (R16).
- Semantic float sums use `math.fsum`, including behavioral sites in dispatching rules and samplers (R17); seeded results differ from 0.12 anyway.
- `FleetCoordinator.create_order` attaches the order at once, so only `submit` and `cancel` are deferred before activation (R18).
- Submit-then-cancel before activation means "dispatched at t=0, then cancelled" (R19).
- Benchmark `none` runs without the default metrics (R26); `digest` and `full` share that shop (R27); the intralogistics workload has no 0.12.0 side (R28).

**Items the final review should triage** (a selection from the run's deferred minors, not the full list): late `job.queue_left` events emitted when an environment is dropped (consider making `emit` a no-op after `Environment.close()`); `Environment.__init__` leaks earlier sink file handles if a later sink fails to attach; KPI samples are not flushed on a latency seal, so a crash loses the last interval's; KPI declarations are not stored in the trace (an SP3 item); `Trace` public fields should become getters before SP3; the writer's backpressure warning is timing-dependent, so same-seed traces can differ in `(t, seq)` cursors under backpressure; an order-dependent test (`test_digest.py::test_projection_layout_and_framing` fails when run with only two other test files); filtered-out DEBUG messages still build a `LogEvent` and consume a `seq`; API pages render private members; `import simulatte` now imports the runner (multiprocessing, tqdm; about 15 ms; the Pyodide smoke check was not run); the observer-invariance suite does not check that observers schedule no extra SimPy events and its `none` configuration asserts six event types only; CPython 3.11 is not covered by CI although `requires-python` allows it, and its `sum()` differs from 3.12+ (moot after D58).

**Benchmark follow-ups:**
- Calibrate the noise bands on the first CI runs (they come from eva); the new rows give a first look at `default`, `default_logging`, `kpi` and the intralogistics ratios on the runners.
- The CPython `full` budget is 5.3× (D60, §7.1); re-measure `u90-50k` with the `server.work_credited` event and revisit if it fails on the runners.
- Fix the saturated-fleet slowdown (§7.2 and the next item).
- Add a workload that logs at INFO, so that `default_logging` measures logging cost and not only subscription bookkeeping.
- The seek benchmark clears the reader cache before each seek and so measures the cold path only; warm seeks, the TypeScript reader's speed and end-to-end replication throughput (B20) are not covered here.
- A cross-version intralogistics comparison needs a shim over the 0.12.0 API (R28).
- **Saturated-fleet slowdown (pre-existing, not fixed in SP1).** `FleetCoordinator._check_pending_queue` (`src/simulatte/intralogistics/fleet.py`, lines 1165 to 1198) scans the whole pending backlog and the fleet three times per order, on every mission end and on every `pending_retry_delay` (1.0) tick of `_pending_retry_loop` (lines 1205 to 1209): O(backlog × fleet) per call. Measured with 20 AGVs and an order every 15 to 30 time units: half a shift 3.5 s (backlog 198), one shift 24.5 s (backlog 666), two shifts more than 2 minutes. Candidate fixes: return at once when no AGV is idle, compute the idle-capable AGVs once per call, or make the retry event-driven.

## 9. Reproducing

```bash
uv venv /tmp/head --python 3.14 && uv pip install --python /tmp/head .
uv pip freeze --python /tmp/head | grep -v '^simulatte' > /tmp/constraints.txt
uv venv /tmp/base --python 3.14 && uv pip install --python /tmp/base simulatte==0.12.0 --constraint /tmp/constraints.txt
cd benchmarks; W=workloads/jobshop10-u90-5k.json; export MPLBACKEND=Agg
for m in none default default_logging bare kpi digest full; do
  /tmp/head/bin/python run.py --mode $m --workload $W --warmup 1 --repeat 5 --processes 3 --json /tmp/head-$m.json
done
/tmp/head/bin/python compare.py /tmp/head-bare.json /tmp/head-default_logging.json     # logging cost
/tmp/head/bin/python compare.py /tmp/head-none.json /tmp/head-kpi.json                # kpi ratio
for m in none default default_logging bare kpi digest full; do
  /tmp/head/bin/python intralogistics.py --mode $m --warmup 1 --repeat 3 --processes 2 --json /tmp/il-$m.json
done
```

The 50,000-job runs used the regenerated files (§3) with one process and a single configuration per call, for example:

```bash
/tmp/head/bin/python run.py --mode full --workload /tmp/jobshop10-u90-50k.json --warmup 1 --repeat 3 --processes 1 --seeks 500 --json /tmp/head-full-50k.json   # warm-up 2 on PyPy
```

On PyPy use `--warmup 8` for the CI-size job-shop workloads and `--warmup 2` for the 50k and intralogistics ones. Pooling over rounds, as in §3, was done with a scratch script that concatenates the `samples_s` of the result files of each configuration; it is not committed.
