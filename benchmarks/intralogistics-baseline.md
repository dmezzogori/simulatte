# Intralogistics comparison with 0.12.0

`intralogistics.py` runs the same advanced-example workload on released `simulatte==0.12.0` and the current
branch. Cross-version modes are `none`, `default` and `default_logging`; `bare`, `kpi`, `digest` and `full`
require SP1. These comparisons are report-only.

## Compatibility and equal work

The shim translates warehouse `pick_time`/`put_time` and AGV `load_time`/`unload_time` to the release's
`*_time_fn` arguments. Durations, capacities, layout, dispatch strategy and replenishment policy are identical.
The release has no environment RNG streams, so the shim implements SP1's `simulatte-rng-v1` seed derivation
for the same three outbound streams: SKU/quantity, interval and due-date offset. It draws through the same
`random.Random` methods in the same order. Run both versions with the same interpreter and dependency versions;
the benchmark does not promise that CPython and PyPy draw identical sequences.

`default` uses each version's native EMA collector with alpha 0.01. The release always calls its collector's
`record(order)` method and replaces `None` with an EMA collector, so `none` and `default_logging` supply a
no-op collector. Its method-call overhead remains in the measured release; no installed package is patched.
On the branch these modes use `default_metrics=False` and check that no domain-event subscriber is attached.
All common modes retain the default INFO logging. This workload does not deliberately emit INFO messages;
`default_logging` is the same configuration as `none`, not a measurement of message formatting or I/O.

Results include both an input hash and a lifecycle trajectory hash. The hashes cover all submitted orders,
including replenishment: submission index, SKU, quantity, warehouse names, arrival and due date; the trajectory
also includes status, dispatch/pick/delivery times and assigned AGV. Submission indices replace 0.12.0's random
UUID order ids. Zero-count statuses are omitted because SP1 adds `PENDING_ACTIVATION`. No physical inputs,
nonzero counts, lifecycle times or assignments are normalized away. Repeats and fresh processes must agree,
and `compare.py` rejects different counts or hashes. Workload version `intralogistics-advanced-v2` prevents
comparing these enriched hashes against earlier benchmark outputs.

There are no injected breakdowns or cancellations in this workload. Deliberately adding them can change
trajectories because the pre-0.13 fixes repair recovery and terminal-order behavior. Such mismatches should
remain failed comparability checks; do not remove fields to make differing runs appear equivalent.

## Commands

Use the two same-interpreter environments described in [README.md](README.md), then run from `benchmarks/`:

```bash
for mode in none default default_logging; do
  /tmp/base/bin/python intralogistics.py --mode "$mode" --shifts 1 \
    --warmup 1 --repeat 3 --processes 2 --json "/tmp/il-base-$mode.json"
  /tmp/head/bin/python intralogistics.py --mode "$mode" --shifts 1 \
    --warmup 1 --repeat 3 --processes 2 --json "/tmp/il-head-$mode.json"
  /tmp/head/bin/python compare.py "/tmp/il-base-$mode.json" "/tmp/il-head-$mode.json" --report-only
done
```

The CI-scale workload uses `--shifts 10`. Retain all raw JSON files, which include interpreter, hardware,
configuration, hashes, samples and per-process medians. The default separate memory probe works on both versions.

## Local verification, 2026-10-10

On Apple M1 Pro (10 cores), CPython 3.14.1, with the release constrained to the branch's dependency versions:
20 AGVs, one shift, intervals 30–60, one warm-up and three measured repeats in each of two fresh processes.
The six cross-version/mode results have exactly 1,018 submitted orders and 1,011 completed orders, with identical
input hash `3278fbdc39deac95395c35aaba077ba50193846c62f858111b41feb11035acfd` and trajectory hash
`b2a2ca3dbda4c64ab2d5ceb1665018c4288bcb8368e3115e94f6ec700a90bf98`.

| Mode | 0.12.0 median (IQR), seconds | Branch median (IQR), seconds | Branch / release |
|---|---:|---:|---:|
| none | 0.19654 (0.00351) | 0.25376 (0.00308) | 1.291 |
| default | 0.19676 (0.00053) | 0.27684 (0.01142) | 1.407 |
| default_logging | 0.18915 (0.00934) | 0.24939 (0.00844) | 1.318 |

These short local runs verify the shim and expose the measured overhead; they do not calibrate a CI budget.
A 0.1-shift full-trace run on the branch also reproduced the release's counts and trajectory. The release rejects
trace-only modes with an explicit CLI error.

The CI-size 10-shift smoke run also matched exactly: 10,979 submitted orders and 10,969 completed,
with lifecycle hash `79d15c6fd5acc342e5c91d88c893e0f85406c98fd16d6d377447a7dc14888bf9`.
Both versions also passed a separate-process memory-probe smoke run.
