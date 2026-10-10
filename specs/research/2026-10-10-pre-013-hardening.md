# Pre-0.13 hardening verification

Date: 2026-10-10. Scope: issues #44–#55, authorized by Davide before tagging 0.13.
This report supplements the SP1 final report; it does not replace its historical measurements.

## Runner noise calibration (#55)

Inputs are the uploaded `bench-3.14` and `bench-pypy-3.11` JSON artifacts from:

- [PR #43 run 38044214438](https://github.com/dmezzogori/simulatte/actions/runs/38044214438)
- [First successful main run 38047000941](https://github.com/dmezzogori/simulatte/actions/runs/38047000941), commit `3cb6937`.

Each workload/version/interpreter has three independent processes and five timed repetitions per process.
`benchmarks/calibrate_noise.py` resamples the process medians independently for head and baseline, takes the ratio
of their medians and reports its change from the observed ratio. The seed is 20261010, 10,000 bootstrap draws,
and the interval uses the 2.5th and 97.5th percentiles. Individual within-process repetitions are not independent
resampling units. These are empirical intervals from small samples, not guarantees for future hosted runners.

| Run | Runtime | Largest upward ratio uncertainty | Largest downward uncertainty |
|---|---|---:|---:|
| PR #43 | CPython | 1.21 percentage points | 1.56 pp |
| main | CPython | 0.90 pp | 0.92 pp |
| PR #43 | PyPy | 6.22 pp | 1.76 pp |
| main | PyPy | 4.20 pp | 12.80 pp |

Calibration: retain the 2% CPython band; set both PyPy noise bands to 7%, rounding the observed upper bound up
with 0.78 percentage points of room. Accepted overhead budgets remain unchanged. The old laptop-derived 5%
PyPy band did not cover the PR runner's measured upward uncertainty. The large downward main interval comes
from a slow stripped-baseline process and does not create a false gate failure; using its full range would
unnecessarily widen the allowance. Revisit these bands as more runner batches accumulate.

The main run passed both original gates. CPython `none` was −24.21% / −23.49% against released 0.12.0 and
+7.67% / +8.41% against the stripped baseline (u90/u95); u95 had only 0.09 percentage points of margin against
the 8.5% total threshold. PyPy was −29.13% / −26.38% against released and −9.97% / −10.57% against stripped.
The CI report-only modes remain reports: calibration does not promote them to required gates.

## Saturated fleet (#44)

A fixed 20-AGV workload submits an order every 0.1 simulation units. An isolated `git archive` of the base
commit and the fixed checkout run the same model with no subscribers. Timings are diagnostic local probes:

| Orders | Base | Fixed |
|---:|---:|---:|
| 1,000 | 0.333 s | 0.0116 s |
| 2,000 | 1.336 s | 0.0225 s |
| 4,000 | 5.656 s | 0.0526 s |

Counts match at every size; at 4,000 orders both have 340 completed and 3,640 pending. The fix skips scans when
no capable idle AGV exists, reuses the idle fleet for built-in strategies and avoids shifting the pending list
on each head removal. Custom strategies retain their complete-fleet input and polling behavior.

## Intralogistics versus 0.12.0 (#55)

The compatibility shim was run against the published 0.12.0 wheel on CPython 3.14.1, Apple M1 Pro.
One shift, 20 AGVs, one warm-up, three repetitions in each of two independent processes:

| Mode | 0.12.0 | Fixed branch | Ratio |
|---|---:|---:|---:|
| none | 0.19654 s | 0.25376 s | 1.291 |
| default | 0.19676 s | 0.27684 s | 1.407 |
| default_logging | 0.18915 s | 0.24939 s | 1.318 |

Every result has 1,018 orders, 1,011 completions, the same input request hash (`3278fbdc…`) and lifecycle
trajectory hash (`b2a2ca3d…`). The shim reproduces SP1 stream seeds on 0.12.0, maps renamed timing parameters,
and normalizes legacy UUIDs to submission order. Legacy `none` retains the mandatory no-op collector callback
cost. The benchmark has no accepted cross-version budget; CI reports it without gating. This workload has
no interruptions and therefore compares an unchanged trajectory despite the intentional recovery corrections.

## New measurement paths

- `run.py --log-info`: one INFO arrival log per job to a temporary file, compared with the same calls and closed
  sinks in `bare`; CI now runs this matched pair. Original non-logging workloads are unchanged.
- `run.py --mode full`: cold seeks plus immediate warm-cache repetitions of the same cursor.
- `studio/packages/trace` benchmark: open, strict wire decode, cold and warm prepare/replay, with JSON output.
- `bench_replications.py`: complete Runner throughput including process startup/teardown and result extraction;
  verifies identical per-seed outcomes between sequential and parallel execution.
- Observer-invariance tests count processed SimPy steps and reject every domain subscription in `none`.
- CI adds CPython 3.11 alongside the existing runtimes.


## Full-size recording and reader measurements (#55)

CPython 3.14.1 on Apple M1 Pro; `jobshop10-u90-50k.json` regenerated with 10 servers, 50,000 jobs,
utilization 0.9, seed 3. One warm-up and three timed runs, no memory probe:

| Measure | Result |
|---|---:|
| `none` median | 6.3373 s |
| `full` median (IQR) | 33.2362 s (0.1986 s) |
| `full / none` | **5.2445×**, below D60's 5.3× |
| Trace | 80,603,156 bytes (1.612 KB/job), 371 chunks |
| Python open | 7.08 ms |
| Python cold seek p50 / p95 | 49.92 / 68.05 ms |
| Python warm seek p50 / p95 | 4.49 / 9.08 ms |

The 30 seek cursors use seed 0 and uniformly sampled times. Counts and trajectory hashes match between
recorded and unrecorded runs. The budget margin is small (about 1% of the allowed ratio); these are local
measurements, not a new CI gate or a guarantee on every machine. Sealed chunk counts can vary with latency.

The same file on Node 24.13.0, five iterations and eight chunk-end cursors (40 seek samples), gave:

| TypeScript measure | Median / p95 |
|---|---:|
| Open, in-memory source | 2.93 / 4.26 ms |
| Decode first 1,049,524-byte uncompressed chunk | 21.84 / 34.05 ms |
| Cold prepare + stateAt | 34.18 / 47.04 ms |
| Immediate warm prepare + stateAt | 9.53 / 13.95 ms |

File reads are excluded; cold means empty reader cache. The Python and TypeScript cursor sampling methods
are different, so their numbers are not a controlled language comparison. Raw local reports were written to
`/tmp/simulatte-50k-{none,full}.json` and `/tmp/simulatte-ts-50k.log`; rerun the documented commands for durable
artifacts on another host.

Runner smoke (four replications, horizon 100, six servers, two workers, one repetition): sequential 0.0474 s,
parallel 0.1801 s, matching result hash. This deliberately small run verifies startup-inclusive measurement;
it does not establish the crossover where parallel execution becomes worthwhile.

## Integrated verification and remaining follow-ups

- CPython 3.14: 1,572 tests passed; branch coverage 99.41% against the 99% gate.
- CPython 3.11: the same 1,572 tests passed in a separate frozen-lockfile environment.
- TypeScript: 203 tests and `tsc --noEmit` passed, including strict wire decoding and immutable metadata.
- Ruff lint/format, `ty check src`, lockfile check, Zensical build and the 51-page documentation-link gate passed.
- Cross-language replay checked a Python trace containing initial and late KPI declarations, including a
  trailerless copy. Historical fleet parity data remains intact; changed interrupted-recovery results live in
  a separate fixture with provenance.

Two existing parking-wait cases remain outside #48's already-parked entry fix: simultaneous duplicate
`ParkingArea.enter` calls at capacity one leave the second queued and it can repark after `leave`; an
interrupted pending parking request also needs cancellation cleanup. Neither is required to verify the
reported repeated entry of an already-parked AGV. They should be addressed as separate follow-up issues.

No version bump, release date or tag is included. The maintainer's release checklist remains #56.
