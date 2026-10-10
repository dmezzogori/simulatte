# Task 10 benchmark evidence

Measured source: `6a3f131d8eec99d6552ed4c5ddf5807a3beea104`; installed head package retains `0.13.0`. Raw root: `/tmp/simulatte-sp2-bench.jsKjEr`. The exact executed commands, cwd, explicit environment overrides, individual exit statuses, timestamps and log paths are in `commands.jsonl` and the readable `command-ledger.md`; `benchmark-harness.py.txt` preserves the sequential local harness. Durable copies rename `.log` outputs to `.txt`; original paths remain in the command ledger. The metadata copy retains hardware/runtime and relevant background load; the unrelated process inventory remains only in the local raw capture. No timing reruns, altered noise bands, budget changes or excluded modes.

Machine: eva.local, Apple M1 Pro, 10 CPUs, 17,179,869,184 bytes RAM, macOS 27.0.1 (26A434), arm64. CPython 3.14.1 (Clang 21.1.4); PyPy 7.3.18 / Python 3.11.11 (Apple LLVM 15.0.0). uv 0.13.0, Node v24.13.0, pnpm 11.5.1. Local PyPy differs from historical issue #59 (PyPy 8.0.0/Python 3.11.16); this evidence does not reproduce that exact environment or establish unexecuted hosted CI lanes.

Each runtime used separate non-editable `head` and released `base` environments, with matching constrained shared dependency versions. `stripped` is a separate package copy imported with PYTHONPATH under the released interpreter. All six pre-measurement import assertions passed. Runtime/version/path logs, package freezes, constraints and source hashes are retained. No primary `.venv` or global environment changes.

The first CPython base install exited 2 before measurement because the local wrapper captured uv informational stderr together with dependency freeze stdout. Original error retained in `cpython/install-base.log`; a quiet freeze produced clean constraints, corrected install passed, and import assertions passed before timing. This was a setup correction, not a benchmark rerun.

CPU-heavy acceptance work was deferred during timings. There was a brief gap after the completed CPython digest child, and a longer gap between runtime lanes for acceptance checks; no measured child was paused or overlapped those checks. `coordination.jsonl` records the short gap; the digest wrapper elapsed includes that parent-orchestration pause, while JSON samples do not. Existing desktop/background activity is a local limitation: a snapshot showed Ghostty around 51% CPU. No unrelated processes were controlled. Process snapshots are retained.

## Exact no-subscriber gates

CPython warmup/repeat/processes = 1/5/3; PyPy = 8/5/3. Memory probes retained by the canonical driver, separately from timings.

| Runtime | Workload | Baseline | Base median s | Head median s | Head/base | Overhead | Budget + noise | Exit |
|---|---|---|---:|---:|---:|---:|---:|---:|
| cpython 3.14.1 | jobshop10-u90-5k | base | 0.865073 | 0.628015 | 0.7260 | -27.40% | 3% + 2% | 0 |
| cpython 3.14.1 | jobshop10-u90-5k | stripped | 0.593735 | 0.628015 | 1.0577 | +5.77% | 6.5% + 2% | 0 |
| cpython 3.14.1 | jobshop10-u95-5k | base | 0.908743 | 0.673689 | 0.7413 | -25.87% | 3% + 2% | 0 |
| cpython 3.14.1 | jobshop10-u95-5k | stripped | 0.634065 | 0.673689 | 1.0625 | +6.25% | 6.5% + 2% | 0 |
| pypy 3.11.11 | jobshop10-u90-5k | base | 0.521108 | 0.317946 | 0.6101 | -38.99% | 3% + 7% | 0 |
| pypy 3.11.11 | jobshop10-u90-5k | stripped | 0.401398 | 0.317946 | 0.7921 | -20.79% | 3% + 7% | 0 |
| pypy 3.11.11 | jobshop10-u95-5k | base | 0.504099 | 0.332529 | 0.6597 | -34.03% | 3% + 7% | 0 |
| pypy 3.11.11 | jobshop10-u95-5k | stripped | 0.411521 | 0.332529 | 0.8080 | -19.20% | 3% + 7% | 0 |

## Report-only job-shop measurements

All rows remain report-only. Historical accepted targets are context, not new gates; issue #59 remains accepted and is not fixed by this task. Full raw timing/IQR/memory/trace/seek details are in each lane `details.log`.

| Comparison | CPython ratio | PyPy ratio |
|---|---:|---:|
| head default / base default | 0.7574× | 0.6267× |
| head default / stripped default | 1.0951× | 0.7963× |
| head default_logging / base default_logging | 0.7439× | 0.6178× |
| head default_logging / stripped default_logging | 1.0881× | 0.7896× |
| head default / none | 1.0652× | 0.9906× |
| head default_logging / bare | 1.0221× | 1.0058× |
| head kpi / none | 3.4598× | 3.9055× |
| head digest / none | 3.0962× | 3.8116× |
| head full / none | 5.1556× | 6.1984× |
| head INFO default_logging / INFO bare | 1.1088× | 1.1136× |
| sampling head / released | 0.6628× | 0.2307× |

Historical recording targets: CPython kpi 3.7× / digest 3.2× / full 5.3×; PyPy kpi 4.5× / digest 4.3× / full 7×.

## Report-only intralogistics

Exact CI shifts 10, repeat 3, no-memory; warmup 1 CPython or 2 PyPy. Every head mode and released none/default/default_logging ran; cross-version comparisons explicitly used `--report-only`. No adopted intralogistics budget.

| Comparison | CPython ratio | PyPy ratio |
|---|---:|---:|
| head default / head none | 1.0822× | 1.0401× |
| head default_logging / head none | 1.0143× | 1.0033× |
| head bare / head none | 1.0034× | 1.0302× |
| head kpi / head none | 2.3648× | 2.6329× |
| head digest / head none | 2.1612× | 2.4750× |
| head full / head none | 2.9608× | 3.8171× |
| head none / released none | 1.3200× | 1.0739× |
| head default / released default | 1.4159× | 1.0417× |
| head default_logging / released default_logging | 1.3384× | 1.0369× |

## Replication and calibration reports

Replication command: isolated head Python `benchmarks/bench_replications.py --replications 8 --horizon 1000 --workers 2 --repeat 3`. Startup/teardown is included.

| Runtime | Sequential median s | Parallel median s | Sequential / parallel | Trajectories |
|---|---:|---:|---:|---|
| cpython | 0.745185 | 0.481206 | 1.5486× | Equal |
| pypy | 1.902708 | 1.044717 | 1.8213× | Equal |

Calibration used the completed two artifact directories, defaults: 10,000 resamples and process medians as units. Three processes provide limited evidence and authorize no noise-band change.

| Runtime | Workload | Baseline | Ratio uncertainty 95% interval (percentage points) |
|---|---|---|---:|
| cpython | jobshop10-u90-5k | base | [-0.956, +0.582] |
| cpython | jobshop10-u90-5k | stripped | [-2.994, +0.663] |
| cpython | jobshop10-u95-5k | base | [-0.709, +0.719] |
| cpython | jobshop10-u95-5k | stripped | [-0.393, +0.650] |
| pypy | jobshop10-u90-5k | base | [-5.532, +0.716] |
| pypy | jobshop10-u90-5k | stripped | [-0.298, +3.502] |
| pypy | jobshop10-u95-5k | base | [-2.610, +0.489] |
| pypy | jobshop10-u95-5k | stripped | [-3.325, +0.458] |

## TypeScript and completion evidence

`pnpm --dir studio/packages/trace benchmark --iterations 30 --cursors 8` ran against the canonical shop_small fixture; full stdout JSON is preserved in `ts-benchmark.log`. This is report-only.


| TypeScript operation | Samples | Median ms | p95 ms |
|---|---:|---:|---:|
| open | 30 | 0.468625 | 0.662792 |
| decode | 30 | 0.116625 | 0.168625 |
| coldSeek | 240 | 0.288458 | 0.570625 |
| warmSeek | 240 | 0.029458 | 0.043000 |

Command ledger contains 142 individual invocations; nonzero statuses: `cpython/install-base` = 2.

There are 30 canonical measurement JSONs per runtime (60 total). All gate configurations/source identities/shared dependency constraints are checked by `artifact-audit.log`. Setup corrections and acceptance timing gaps are documented above. Preparation 1k/10k measurements are owned by the acceptance worker and are not duplicated here. No hosted CI lane or issue #59 fix is claimed.

All eight no-subscriber comparisons passed. Both complete report-only matrices and README reports exited 0. No benchmark blockers remain. The six recording ratios are below the historical report-only targets on these local runtimes; the accepted issue #59 limitation is unchanged because its newer PyPy runtime was not measured here.
