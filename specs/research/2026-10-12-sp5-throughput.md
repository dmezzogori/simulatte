# SP5 throughput measurement: one-slot coordinator replications

Date: 2026-10-12. Branch `feature/sp5-experiments`, built on commit `0025b6c` (simulatte 0.16.0). Harness:
`benchmarks/studio_throughput.py`. Raw data: `specs/research/sp5-data/`. This is the measurement that SP5 must have
before it promises scheduling performance or warm spares (design question Q8).

## 1. What was measured

The coordinator of SP4 has exactly one execution slot, so only `--slots 1` could be measured. For each
configuration one fresh interpreter prepared a generated project (a `@model` that emits N domain events, the model
of `benchmarks/studio_execution.py`), then submitted and awaited 32 replications one after the other. Each uses a
distinct seed (0..31) and the same parameters, level `full`. Afterwards the same process ran the same model, horizon
(N + 1) and seeds through `Runner` (sequential, in-process, no trace), 32 runs of one seed each. The per-replication
overhead is the coordinator wall time per replication minus the Runner wall time per replication, both means.

What the figures contain:

- Replications per minute is 32 divided by the time from the first `submit` to the last `wait` returning. It excludes
  `prepare()` (reported separately in the JSON, 0.5 to 1.0 s) and `close()`.
- Submit to ready: from just before `submit()` (which includes loading the captured snapshot) to the coordinator
  thread reporting `ready`. Timestamps come from a benchmark-only wrapper around `Coordinator._emit`.
- Ready to publication: from `ready` to `publish()` returning.
- Store commit: the time inside `publish()`, measured by a benchmark-only wrapper around the module function. A
  finer hook existed, so `wait`-return timing was not used. `wait` returned 1 to 4 ms after `publish` returned on
  CPython and up to 15 ms on PyPy (JSON key `wait_return_lag`).
- Coordinator CPU per replication: user plus system time of the coordinator process over the loop. Worker CPU is
  only visible when the worker is reaped at `close()`, so `children_cpu_per_execution` is the reaped-children total
  divided by 33 executions (32 replications plus the inspection run of `prepare()`).
- RSS: `ru_maxrss` high-water of the coordinator process (read before the Runner baseline ran), and the largest
  reaped child.

None of this is a warmed-throughput claim. Each cell is one process on a host that was running other work (loads
below), and the two eva batches differ by about 20 to 30 % on the same code and machine (section 3).

## 2. Hosts

| Host | Hardware | Interpreter | Load average (1, 5, 15 min) at the time |
|---|---|---|---|
| eva | MacBook Pro, Apple M1 Pro, 10 cores, 16 GB, macOS 27.0.1 | CPython 3.14.1 via `uv run --all-extras` | 6.3, 9.1, 8.0 before the final batch; 10.7, 9.7, 8.3 after |
| strangelove | Ryzen 7 3800X, 8 cores / 16 threads, 62 GB, Linux 7.0.0 x86_64 | CPython 3.14.0, PyPy 8.0.0 (Python 3.11.16) | 1.1 to 2.0 over the runs (5-minute average 4 to 7 from a teban test suite that had just finished); log in `sp5-data/load.log` |

Both hosts were shared with other work during the runs, so absolute numbers are indicative. strangelove ran from a
copy of the working tree in `/tmp/simulatte-sp5-task1.7TBqYn` (left in place): `.venv` is
`uv sync --dev --all-extras --python 3.14`, `.venv-pypy` is `uv sync --no-dev --python pypy@3.11`. The all-extras
sync does not complete on PyPy (it builds `zensical` from source), and the harness needs only the core
dependencies. The teban host was not touched.

Commands (per configuration; eva with `uv run --all-extras python`, strangelove with the venv interpreters):

```sh
python benchmarks/studio_throughput.py --replications 32 --events {100,20000} --worker <host> --json <file>
```

## 3. Results

Replications per minute (single observations):

| Host, interpreter | 100 events | 20,000 events |
|---|---|---|
| eva, CPython 3.14.1 | 56.7 | 30.1 |
| strangelove, CPython 3.14.0 | 85.9 | 44.7 |
| strangelove, PyPy 8.0.0 | 46.5 (25 of 32 completed) | 33.1 (one of three sequences completed 32) |
| `Runner` in the same process, for reference (mean) | 63,000 to 169,000 | 2,900 to 13,400 |

A first eva batch, run with an earlier revision of the harness that measured the same things (its raw files were
overwritten by the final batch), gave 69.5 and 41.5 replications per minute, with host load around 4. The same code
on the same machine therefore moved by about 20 to 30 % between batches; read the eva figures as a range, not a
point.

Per replication, in seconds (means; maximum in brackets where it differs notably):

| Configuration | Wall | Runner | Overhead | Submit to ready | Ready to publication | Store commit |
|---|---|---|---|---|---|---|
| eva CPython, 100 | 1.057 | 0.0004 | 1.057 | 0.646 (1.26) | 0.410 (0.59) | 0.012 (0.022) |
| eva CPython, 20,000 | 1.996 | 0.0173 | 1.979 | 0.767 (1.17) | 1.228 (2.75) | 0.426 (0.83) |
| strangelove CPython, 100 | 0.698 | 0.0004 | 0.697 | 0.501 (0.55) | 0.194 (0.23) | 0.016 (0.029) |
| strangelove CPython, 20,000 | 1.341 | 0.0206 | 1.320 | 0.492 (0.55) | 0.846 (0.88) | 0.426 (0.45) |
| strangelove PyPy, 100 (25 completed) | 1.288 | 0.0010 | 1.287 | 0.991 (1.05) | 0.293 (0.35) | 0.050 (0.109) |
| strangelove PyPy, 20,000 | 1.813 | 0.0045 | 1.809 | 0.992 (1.03) | 0.818 (1.10) | 0.305 (0.52) |

The first replication is not noticeably colder than the rest (the worker process is reused; throughput excluding
the first replication differs by under 1 % everywhere). Every replication, including the second, pays a submit to
ready of about 0.5 s on CPython: the execution process is created per request.

CPU and memory:

| Configuration | Coordinator CPU per replication | Worker CPU per execution | Cores busy in a one-slot flow | Coordinator RSS high-water | Largest child RSS |
|---|---|---|---|---|---|
| eva CPython, 100 | 0.042 s | 0.484 s | 0.50 | 43 MiB | 53 MiB |
| eva CPython, 20,000 | 0.411 s | 0.696 s | 0.55 | 62 MiB | 59 MiB |
| strangelove CPython, 100 | 0.046 s | 0.467 s | 0.74 | 40 MiB | 42 MiB |
| strangelove CPython, 20,000 | 0.461 s | 0.747 s | 0.90 | 62 MiB | 49 MiB |
| strangelove PyPy, 100 | 0.297 s | 0.892 s | 0.92 | 209 MiB | 106 MiB |
| strangelove PyPy, 20,000 | 0.547 s | 1.183 s | 0.95 | 283 MiB | 121 MiB |

Cores busy is (coordinator CPU + worker CPU) divided by wall time per replication.

What the numbers say:

- Replication cost is dominated by process and storage cost, not simulation. The simulation itself takes 0.4 ms
  (100 events) or 17 to 21 ms (20,000 events) in `Runner`; the coordinator adds 0.7 to 2.0 s. The 20,000-event
  trace adds 0.6 s (strangelove) to 0.9 s (eva) to the overhead on CPython, mostly in ready to publication.
- At 20,000 events the store commit takes 0.43 s (0.30 s on PyPy) on every replication, and it accounts for most of
  the coordinator's CPU: coordinator CPU per replication (0.41 to 0.55 s) is close to the commit time. The commit
  runs in the coordinator thread, so it is serialized across anything that publishes through one coordinator. With
  100 events it takes 12 to 16 ms.
- Submit to ready does not depend on the event count (0.49 to 0.50 s on strangelove at both sizes), so it is
  startup, not simulation.
- The worker is the larger share of CPU (0.47 to 0.75 s per execution on CPython). At 100 events that is almost
  entirely startup, since the simulation uses under 1 ms.
- A one-slot flow keeps 0.5 to 0.9 cores busy on CPython; the rest of the wall time is waiting (process creation,
  I/O, polling intervals in the transport).
- PyPy is slower than CPython here (cold interpreter, JIT does not help 0.5 s processes) and needs 4 to 5 times the
  coordinator memory.

### PyPy failures

On PyPy 8.0.0 only some sequences completed. Final batch (retained in `sp5-data/`): at 100 events the sequence
stopped after 25 replications; at 20,000 events the first attempt completed 32, the second stopped after 3, the
third after 20. The failed replication reports `validation_failed` with `phase: preparing` and, in the child,
`TypeError: initializer for ctype 'blake_state *' must be a compatible pointer, not cdata 'unsigned int *'` raised by
`hashlib.blake2b(digest_size=32)` in `DigestAccumulator.__init__` (`src/simulatte/digest.py`), using PyPy's cffi
`_blake2`. The error is intermittent and appears after a number of successful replications in the same coordinator.
During development, before the harness recorded failures, I also saw two other failures of 20,000-event PyPy
sequences ("Invalid child channel token", and a JSON decode error "Unexpected 'G'"). Those were not retained and I
did not establish whether they share the cause. The failure is in the shipped code path (the model build in the
child), not in the harness, and a single run of `benchmarks/studio_execution.py --events 20000` on PyPy passed four
times in a row, so it needs repetition to show. The PyPy rows above come from completed replications only. It is
unrelated to issue #59 (PyPy recording overhead) as far as I can tell. I did not investigate further.

## 4. Limits of this evidence

- One process per configuration, a single observation each, on shared hosts. The eva spread between two batches
  (section 3) is the best available estimate of how much to trust a cell.
- `--slots 2` and `--slots 4` were not measured: they need the SP5 scheduler and the script exits with a message.
  Task 4 re-runs them on the same two hosts.
- The decomposition of submit to ready is not measured. I measured only the cost of starting an interpreter and
  importing `simulatte` and the child module: median 0.106 s on eva, 0.081 s on strangelove CPython and 0.202 s on
  strangelove PyPy (10 spawns each, `python -c "import simulatte; from simulatte.execution import child"`, outside
  the coordinator). That is 16 % of the measured 0.49 to 0.65 s on CPython; the remainder (snapshot
  materialization, request validation, model import and build, INITIAL trace and its receipt) is unattributed.
- Runner runs in the same process after the coordinator and uses `Environment` without trace, digest or isolation,
  so the overhead column is the whole cost of the coordinator path, not only of publishing.
- Event timestamps and the `publish` wrapper are benchmark-only monkeypatches, applied in the coordinator process
  and removed at the end. They add two `perf_counter` calls per replication.

## 5. Proposed default local slot count for 0.17

Proposal: default to 2 local slots, capped at `max(1, cpu_count - 1)`, configurable upward. Reasoning from the
measurements:

- One slot leaves capacity unused: 0.5 to 0.9 cores busy on CPython, with most idle time in startup and I/O, so a
  second slot can overlap another replication's startup and teardown with a running one.
- Coordinator thread work is serialized (store commits and receipt handling run in one thread under one GIL). Its
  CPU per replication divided by wall per replication is 0.04 to 0.07 at 100 events, 0.21 (eva) to 0.34
  (strangelove) at 20,000 events. If that CPU stays serialized, the coordinator saturates at about 25 slots at 100
  events but only 3 to 5 slots at 20,000 events (2.9 on strangelove CPython, 4.9 on eva). Two slots stay under that
  limit for the larger traces; four would sit at or past it on strangelove. This is a projection from one-slot
  figures, not a measurement, and the commit time may grow under contention.
- Each extra slot costs one worker and one execution process: 40 to 60 MiB peak per process on CPython, about twice
  that on PyPy, which is small against 16 to 62 GB hosts.
- A default of 2 keeps the machine usable for Studio, a browser and an editor (each execution process uses up to
  about 0.9 core while running).

Task 4 should re-run `--slots 2` and `--slots 4` for both event sizes on both hosts. If measured throughput at 4
slots is clearly above 2 at 20,000 events, raise the default; if the store commit grows with contention, the 2
stands.

## 6. Warm spare per slot

Recommendation: no, not for 0.17.

Expected saving, derived from the measured submit to ready: the whole of submit to ready is the ceiling, 0.49 s on
strangelove CPython and 0.65 to 0.77 s on eva (0.99 s on PyPy). Against the per-replication wall time of 0.70 and
1.06 s at 100 events, that ceiling is 70 % and 61 %; at 20,000 events, against 1.34 and 2.0 s, it is 37 % and 38 %.
But the only part I can show a spare would remove is interpreter start plus importing `simulatte` and the child
module, 0.08 to 0.11 s on CPython. That is 8 to 15 % of wall at 100 events and 4 to 8 % at 20,000 events. The
rest of submit to ready depends on the request (the snapshot, the model, the build, INITIAL) and a spare cannot
prepare it before the request exists. A spare would also hold an idle process per slot (40 to 60 MiB on CPython,
more on PyPy) and add a state that cancel, timeout and runtime-mismatch handling must cover.

With two or more slots, startup of one replication overlaps the running of another, which already hides part of
this cost. The decision should be revisited only with a measurement that splits submit to ready into spawn,
import, snapshot, build and INITIAL, taken together with the Task 4 multi-slot runs. No spare was implemented.

## 7. Task 4 re-run with the SP5 scheduler

Date: 2026-10-13. Branch `feature/sp5-experiments`, working tree of Task 4 (multi-slot scheduler, uncommitted at
measurement time; the harness is the version committed with it). Host: eva only (CPython 3.14.1, macOS, 10 cores),
load average 2.1 to 2.6 throughout (`sp5-data/task4-run.log`). strangelove was not re-run: it needs a fresh copy of
the working tree and virtual environments, and the plan made it optional for this step. Raw data:
`sp5-data/throughput-eva-cpython-3.14-events{100,20000}-slots{1,2,4}-scheduler.json`.

What changed in the harness: the 32 replications are now one experiment (one configuration, seeds 0..31, level
`full`) started with `start_experiment` on `SlotConfig(local=N)` and awaited with `wait_experiment`, so `--slots 2`
and `--slots 4` run. A replication's wall time runs from its dispatch (the scheduler's `queued` event) to `publish`
returning; throughput runs from `start_experiment` to `wait_experiment` returning and excludes `prepare()`. Each
cell is still one process and a single observation.

```sh
PYTHONPATH=src uv run --all-extras python benchmarks/studio_throughput.py --replications 32 \
  --events {100,20000} --slots {1,2,4} --worker eva --json <file>
```

Replications per minute (all 32 completed in every cell, no failures; every slot ran 8, 15 to 17, or 32 of them):

| Events | 1 slot | 2 slots | 4 slots |
|---|---|---|---|
| 100 | 67.7 | 143.1 | 285.5 |
| 20,000 | 44.8 | 83.3 | 125.8 |

Per replication, in seconds (mean, maximum in brackets):

| Events, slots | Wall | Dispatch to ready | Ready to publication | Store commit | Coordinator CPU | Cores busy |
|---|---|---|---|---|---|---|
| 100, 1 | 0.881 | 0.453 (0.82) | 0.428 (0.65) | 0.017 (0.030) | 0.051 | 0.53 |
| 100, 2 | 0.816 | 0.427 (0.74) | 0.389 (0.55) | 0.011 (0.039) | 0.033 | 1.00 |
| 100, 4 | 0.780 | 0.422 (0.68) | 0.358 (0.49) | 0.010 (0.016) | 0.031 | 2.08 |
| 20,000, 1 | 1.336 | 0.423 (0.61) | 0.913 (1.27) | 0.326 (0.357) | 0.361 | 0.67 |
| 20,000, 2 | 1.365 | 0.486 (0.73) | 0.879 (1.14) | 0.345 (0.548) | 0.327 | 1.18 |
| 20,000, 4 | 1.755 | 0.683 (1.40) | 1.072 (1.57) | 0.351 (0.542) | 0.334 | 1.84 |

Cores busy is (coordinator CPU + worker CPU per execution) × replications per second. Coordinator RSS high-water was
48 to 50 MiB at 100 events and 70 to 87 MiB at 20,000 events; the largest child stayed at 49 to 57 MiB.

What the numbers say:

- At 100 events throughput scales almost linearly with slots (×2.1 at 2, ×4.2 at 4). The one-slot figure, 67.7, is
  above Task 1's 56.7 on the same host; Task 1 already measured a 20 to 30 % spread between batches on eva, and the
  load here was lower (2.5 against 6 to 10), so the difference is not attributed to the scheduler.
- At 20,000 events 2 slots give ×1.9 and 4 slots ×2.8. The per-replication wall time grows from 1.34 s to 1.76 s at
  4 slots (dispatch to ready +0.26 s, ready to publication +0.16 s), so contention appears, but 4 slots are still
  51 % faster than 2.
- The store commit did not grow with slots (0.33 to 0.35 s mean at 20,000 events). Coordinator CPU per replication
  stays at 0.33 to 0.36 s, which at 125.8 replications per minute is 0.70 of a core: the serial projection of
  section 5 (saturation near 3 replications per second, 180 per minute, on eva at 20,000 events) is not reached at 4
  slots.
- Four slots keep about two cores busy on a 10-core host, which leaves room for Studio, a browser and an editor.

Slot default for 0.17: revised from the proposed 2 to **4, capped at `max(1, cpu_count - 1)`**
(`simulatte.execution.slots.default_local_slots`). Section 5 set the rule "if measured throughput at 4 slots is
clearly above 2 at 20,000 events, raise the default"; it is (125.8 against 83.3) and the commit time did not grow.
The cap keeps one core free on small hosts (a 4-core machine gets 3 slots, a 2-core machine 1). Limits: one host,
one observation per cell, CPython only; strangelove (16 threads) and PyPy were not re-measured, and the
coordinator's serial CPU will bound larger slot counts at large traces. Warm spares stay out of 0.17: dispatch to
ready did not shrink with more slots, and section 6's reasoning is unchanged by these runs.
