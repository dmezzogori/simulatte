# Native job-shop prototype contract

Authorized by Davide's 2026-10-15 `/goal`. This contract narrows the feasibility report; implementation and
evaluation are authorized without further design approvals. Production runtime files stay unchanged.

## Question and scope

Evaluate native state/transitions with Python authoring and a Python dispatch hook. Deliver actual current
Simulatte, a small Python reference, and an independent C runtime with thin Python bindings. Default workloads
have 10 capacity-one servers, immediate release, deterministic fixed arrivals/routes/durations/due dates,
FIFO/SPT dispatch and drain stopping. Smaller server counts are permitted for hand-checkable tests.

Inputs have nonnegative finite, nondecreasing arrivals; finite due dates; nonempty routes with distinct valid
server indices; matching strictly positive finite durations. Empty workload is valid. No RNG runs inside the
engine. Reject invalid input and scheduling overflow. Input generation/JSON parsing are outside timings;
backend construction/lowering, run, result extraction and close are inside timings.

Excluded: preemption, cancellation API, zero-duration operations, generator/custom tasks, repeated servers
within a job, material waits, LumsCor, AGVs, production Studio tracing, remote workers, browser builds, and
general-purpose simulation API stability. Deferring these does not establish their feasibility or performance.

## Deterministic event contract

Binary64 time; heap order `(time, increasing insertion sequence)`. Useful events:

1. ARRIVAL_BATCH: announce all jobs at this arrival time in input order; schedule the next distinct batch;
   enqueue this batch's first operations in input order.
2. START: announce start; schedule END at `now + duration`.
3. END: announce completion; schedule ADVANCE at the same time.
4. ADVANCE: free the source server synchronously; schedule RELEASE_DISPATCH for it at this time; immediately
   enqueue the job's next operation, or announce job FINISH.
5. RELEASE_DISPATCH: if free and nonempty, select a waiting operation and grant it.

Every enqueue announces QUEUE and attempts a grant immediately. Grant reserves capacity immediately and
schedules START at the same time. Waiting order is enqueue order, equivalent to `(entry_time, enqueue_seq)`.
FIFO selects the first waiter; SPT selects shortest duration, ties by waiting order. Arrival can therefore
trigger a grant before a pending release callback. Release dispatch is scheduled even when its queue is empty; other inert SimPy signals are omitted.

This compact algorithm was independently checked against unchanged `ShopFloor.main` on 4,000 randomized
FIFO/SPT runs with tied integer times. Durable prototype tests repeat this evidence. The current-Simulatte
adapter must retain actual Environment, Server, ShopFloor.main, ProductionJob and Server.process_job, adding
only observation wrappers and, for hook runs, an explicit grant-time batch policy adapter. Noop current
SimPy signals are unobserved; arbitrary subscribers are outside this contract.

## Common Python interface

`model.py`: frozen `Job(arrival, route, durations, due)` and `Workload(servers, jobs)`;
`.jobs` is a tuple of Jobs, `.servers` a positive int, `.operation_count` the sum of route lengths.
Operations have contiguous zero-based IDs in job/route order; jobs and servers also have zero-based IDs.

Each backend provides `Runner(workload, *, policy='fifo', mode='none', hook=None, trace_path=None)`:
`run()` once; `result()` after success; `close()` idempotent. Repeated run and access after close fail cleanly.
Close works on never-run/failed runners; calls to close/run/result during an active callback are rejected. Counts must be below `0xffffffff` (reserved sentinel). All resources are instance-owned. Result is a detached plain dict:

- `starts`, `ends`: float lists by flattened operation ID; `finished`: float list by job ID.
- `dispatch`: operation IDs in START order; `makespan`, `completed`, `events`, `callbacks`, `records`.
- `kpis`: None in `none`; otherwise `{total_flow, total_wait, total_tardiness, busy, wip_area, max_wip}`.
  `busy` is a float list by server. `events` is diagnostic, backend-specific and not compared for equivalence.

KPIs accumulate during execution: flow=`finish-arrival`, wait=`start-queue_entry`, tardiness=max(0,finish-due),
busy=sum(completed durations); WIP area integrates arrived-not-finished jobs at arrivals/finishes. Aggregates
are compared with tight floating tolerance because arithmetic grouping can vary; all trajectory values,
dispatch order and records must match exactly. Extreme finite inputs may overflow aggregate KPIs to infinity; event times must remain finite and strictly advance. Current default metrics are disabled; prototype KPIs are
independent of the current queue-time accessor's different subtraction formula.

Hook signature: `hook(now, server, candidates) -> operation_id`, where candidates is an immutable tuple of
`(operation_id, duration)` pairs in enqueue order. Call once per actual grant, including singleton choices.
Native built-ins do not enter Python. Hook results must be integer IDs of a supplied candidate (bool invalid).
Exceptions and invalid selections fail the run; all backends must close cleanly. Built-in and Python-hook
FIFO/SPT must make identical choices. `callbacks` counts these explicit batch calls, not internal Python calls
already present in Simulatte. Binding converts candidate data once per decision; its cost remains measured.

## Recording and observation modes

`none`: trajectory arrays/dispatch are still captured for verification, with no KPI accumulation or file.
`kpi`: same outputs plus online KPIs. `record`: KPIs plus a buffered file. Trajectory-output overhead is part
of every measured backend. No semantic digest, compression, checkpoints, indexing or Studio compatibility.

File header is 8 bytes `NEVT0001`, then fixed 32-byte little-endian `<dQIIII` records:
`(time, record_seq, kind, job_id, operation_id, server_id)`. Kinds: 1 ARRIVAL, 2 QUEUE, 3 START, 4 END, 5 FINISH.
ARRIVAL/FINISH use `0xffffffff` for operation/server. Sequence begins at zero. Buffered writes use 64 KiB;
successful run flushes records before exposing results; close releases resources. Flush means OS-buffer delivery, not fsync durability. Native encoding must
not rely on host struct padding. Reader rejects truncated/malformed data; complete-run validation checks
lifecycle, conservation and capacity. Failures may leave a partial file, never reported as a valid result.

## Implementation boundaries and validation

C library owns calendar, queues, numeric state, KPI accumulators, bounded record buffer and all allocations;
it has no Python API dependency. The initial binding uses ctypes with immutable callback arguments and opaque
instance ownership; its total lowering/callback/extraction cost is included. Build a release shared library
locally and standalone AddressSanitizer/UndefinedBehaviorSanitizer executable for lifecycle, ties, inputs,
callbacks and randomized workloads. Keep generated libraries, traces and work inputs ignored locally.

Tests include hand-derived FIFO/SPT results; ties/cross-routing; empty input; invalid input/options; hook
exceptions/bad IDs; repeated lifecycle operations; randomized exact equivalence; observation invariance;
independent trace replay checks and analytical KPI/conservation checks. Sanitizers must exercise C core
allocation, scheduling, queue, recording and callback/error paths. Binding behavior is tested in Python.

## Evaluation and stopping rules

Generate fixed inputs on CPython, then reuse identical hashes across interpreters/backends. Main matrix:
5,000/50,000 jobs × offered load 0.50/0.95 × FIFO/SPT × none/kpi/record × native/Python-hook policies.
Benchmark current Simulatte, reference, C, and optimized Python/PyPy where available. A tuned Python engine
may use heap queues; report architecture and implementation differences openly. Native inputs and outputs
must be equivalent to the baselines, never compared with the earlier LumsCor workload as if identical.

Use uninstrumented serial timings in fresh child processes, warm-ups (more for PyPy), repeated samples;
measure memory separately via fresh-process peak RSS including input/model/output, and report platform units. Close timings include explicit garbage collection after discarding the runner, so Python cyclic object graphs are reclaimed inside the measured lifecycle.
Measure build/run/extract/close and end-to-end sum, jobs/operations per wall second, counts, trace bytes and
hashes. Validate correctness independently and verify output fingerprints for benchmark repetitions.
Report hardware, interpreter/compiler versions and flags, source/input hashes, commands and raw results.

After correct baseline measurements, at most three measured optimization iterations, each with a recorded
hypothesis, before/after equivalent-work measurements and tests. Correctness repairs are documented separately.
Thresholds: 3x native built-in end-to-end throughput and 2x equivalent prototype recorded throughput against
current Simulatte; report against reference and PyPy too. These are evaluation criteria, not mandatory wins.
Native hook results are a separate extensibility assessment. No result demonstrates faster Studio full traces.

Completion: tested implementation, sanitizer pass, reproducible evidence, honest go/no-go or conditional-go
recommendation, local commits and Cortex checkpoint; production diff remains empty.
