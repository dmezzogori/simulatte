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

## AGV traffic release against `main`, 2026-11-22

The branch `feature/agv-traffic` (traffic control, motion and routing, parts A to C) against `main` at `ea1292c`
(0.17.0), on Apple M4 Pro (12 cores), CPython 3.14.3, the same interpreter and dependency versions for both sides
(each tree synced with `uv sync --all-extras` in its own directory; `main` in a temporary `git worktree` outside the
repository, removed afterwards). The branch ran at `5939714`, after the fix wave of the final review, whose node hash
(a `Node` hashes its id only) changed the timings; the workload source is unchanged since part B. The workload is the one
above: 20 AGVs, one shift, intervals 30 to 60, seed 42, `--warmup 1 --repeat 5 --processes 3`. The two sides ran
alternately (main, branch) in five rounds for each mode, one after the other. The machine was not otherwise idle: the
one-minute load average was 2.15 before the first run and 1.7 to 4.1 between the rounds (`uptime` after each round).
`--json` files were kept for every run; each round's median below is that of the 15 timed runs of one invocation.

| Mode | `main` median, s (range over 5 rounds) | Branch median, s (range over 5 rounds) | Branch / `main`, median (range over rounds) | Peak memory `main` / branch, MB |
|---|---:|---:|---:|---:|
| none | 0.18415 (0.18084 to 0.18923) | 0.22042 (0.21753 to 0.22352) | 1.197 (1.173 to 1.208) | 39.0 / 40.5 |
| default | 0.20429 (0.20359 to 0.20571) | 0.24211 (0.24161 to 0.24728) | 1.186 (1.183 to 1.202) | 39.0 / 40.4 |
| default_logging | 0.18834 (0.18658 to 0.18914) | 0.22511 (0.22506 to 0.22586) | 1.196 (1.194 to 1.207) | 39.0 / 40.5 |

The IQR inside a round is 0.001 to 0.005 s on both sides (up to about 2.5 %). The same comparison at `9f0406f`, before
the fix wave, gave 1.274 (1.267 to 1.282), 1.247 (1.240 to 1.258) and 1.273 (1.252 to 1.298), with the branch at
0.24144, 0.26028 and 0.24171 s. `main` reproduces the hashes above (input
`3278fbdc...`, trajectory `b2a2ca3d...`, 1,018 orders, 1,011 completed). The branch has 1,018 orders, 1,012 completed,
input hash `1a5ad97c4bd908101086d8ba16cc592cdbb43c24d768fd85756105ff10e28583` and trajectory hash
`633394be9d9f5b5e07a76a224c14050235a80d5a31a0348488b48a679db95767`, the same in all 15 branch runs of each mode, the
same at `9f0406f` and the same as at the end of part B: part C changes no decision under the default router, and
neither does the fix wave. The two sides do not run the same trajectory
(different hashes), so `compare.py` rejects the pair and the figures above are the medians of the JSON files.

Why the branch is slower. The workload runs under `FreeFlowController` with a `TrapezoidalProfile` that has kinematic
limits, so each travel is planned as runs of arcs (spec §9) where `main` evaluated one rest-to-rest profile per arc.
Part A alone cost nothing measurable on this workload (branch / `main` 0.96 to 0.99 at the end of part A). Part B added
the planning of a run per travel (the speed plan, the affordable-end and battery scans, the larger `piecewise` motion
descriptions, the route state kept per node) and, after its own optimisation pass, cost 22 to 24 % of run time. Part C
adds the router protocol and one router call per ranking (dispatch, bays, chargers, parking). The commit before part C
(`ad4a823`) was run against the branch at `9f0406f` in the same way (five alternating rounds, the same settings, the
same trajectory and hashes in both):

| Mode | `ad4a823` median, s (range) | Branch median, s (range) | Branch / `ad4a823`, median (range) | `ad4a823` / `main` |
|---|---:|---:|---:|---:|
| none | 0.23595 (0.23214 to 0.23691) | 0.24097 (0.23993 to 0.24252) | 1.027 (1.018 to 1.034) | 1.244 |
| default | 0.25582 (0.25385 to 0.25652) | 0.25959 (0.25860 to 0.26321) | 1.019 (1.008 to 1.027) | 1.223 |
| default_logging | 0.23640 (0.23445 to 0.23738) | 0.24155 (0.24107 to 0.24321) | 1.024 (1.016 to 1.030) | 1.247 |

So part C costs 2 to 3 % and part B (with a little of part A) the rest of the 25 to 27 % measured at `9f0406f`; the
node hash of the fix wave brought the total down to 19 to 20 %. Peak memory is 1.4 MB higher
than `main` (peak at `ad4a823`: 40.2 MB). The spec leaves this benchmark ungated (§15). The difference in trajectory
(the 1,012th completed order, the input hash) comes from parking slots being taken (part A) and through-node motion
(part B), recorded in `specs/research/2026-11-21-agv-traffic-acceptance.md`.

Overhead gate of the core workload (CI job `bench`, step "Mode none against released and stripped 0.12.0", CPython
3.14, as the workflow runs it: `--warmup 1 --repeat 5 --processes 3`, budgets `BUDGET_RELEASED` 0.03 with noise 0.02 and
`BUDGET_STRIPPED` 0.065 with noise 0.02), in environments built as the workflow builds them from this checkout:

| Workload | Against | Base median (IQR), s | Branch median (IQR), s | Overhead | Limit | Verdict |
|---|---|---:|---:|---:|---:|---|
| jobshop10-u90-5k | released 0.12.0 | 0.650 (0.005) | 0.483 (0.003) | -25.7 % | +5.0 % | pass |
| jobshop10-u90-5k | 0.12.0 without `env.debug` calls | 0.453 (0.005) | 0.483 (0.003) | +6.7 % | +8.5 % | pass |
| jobshop10-u95-5k | released 0.12.0 | 0.683 (0.007) | 0.518 (0.010) | -24.1 % | +5.0 % | pass |
| jobshop10-u95-5k | 0.12.0 without `env.debug` calls | 0.485 (0.004) | 0.518 (0.010) | +6.7 % | +8.5 % | pass |

The core workload does not run the traffic code, so the gate measures the same unobserved path as before this work.
The PyPy lane of the gate was not run locally (the job runs it on the CI runner).
