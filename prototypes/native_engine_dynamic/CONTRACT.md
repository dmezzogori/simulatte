# Dynamic-job and ConWIP research contract

Davide authorized the hook-boundary experiment followed by dynamic jobs/ConWIP on 2026-11-01.
This is an isolated research milestone. Production runtime, existing static C engine/ABI and historical
evidence remain unchanged. Production Studio encoding is the subsequent separate gate, not part of this
milestone. Design choices below are implementation decisions within that bounded authorization.

Later refinements are documented in the
[simultaneous-event report](../../specs/research/2026-11-02-native-semantics.md)
(initialization and deferred release-policy wake ordering) and the
[live observation contract](../../specs/research/2026-11-02-native-observation.md)
(opt-in event-time views, synchronous boundary delivery and `nd_flush`). The
sections below retain the initial milestone's scope; use those reports for the
subsequent semantics and evidence.

The [no-recording observation study](../../specs/research/2026-11-02-native-observation-untraced.md)
adds live `level="none"`: the final recorder/digest is absent, but the rich
journal remains its observation transport. This differs from the bare engine's
`mode="none"`, which has no Python live collectors or journal.

The [subscriber-interest experiment](../../specs/research/2026-11-02-native-interest.md)
adds optional `interest_checks=True` for untraced live publication. Mandatory view
updates precede the guard. Requested payloads/times remain available; partial
untraced streams can have compressed local sequence numbers. Recording bypasses
the guard, and the original publisher remains the default.

## Interfaces and ownership

`NativeStream` in `native.py` and independent `ReferenceStream` in `reference.py` implement:

```python
Stream(servers, *, wip_cap=None, policy="fifo", mode="none", trace_path=None)
add(job: prototypes.native_engine.model.Job) -> JobHandle
advance(until: float) -> None
inspect(handle: JobHandle) -> dict
run() -> None
result() -> dict
close() -> None
```

Servers and an optional ConWIP cap are positive integers below `0xffffffff`; bool is invalid. FIFO/SPT
dispatch and none/kpi/record modes retain their meanings. Path is required exactly in record mode.
Only exact validated Job instances are accepted; job route indices must fit this stream's servers.
Arrivals must be nondecreasing in registration order and at least the current stream time. Times are
finite binary64; positive durations must advance time without overflow.

Construction starts empty. `advance(t)` processes events strictly before t, then sets the current time
to t, leaving t's events pending. This lets Python create new jobs at t while earlier jobs remain live.
It is legal to add after the calendar temporarily drains while input remains open. `run()` seals input,
drains the calendar, verifies completion and flushes recording; it is permitted only once. Results are
available only after successful run and before close. Add/advance/inspect require an open, unfailed
stream; add/advance fail after run. Close is idempotent, including never-run and failed streams.

IDs are monotonically assigned, zero-based, never reused within one stream, and exclude the sentinel.
JobHandle is an opaque Python-owned owner/id pair, not a C pointer; inspect rejects foreign handles and
handles after close. C owns copies of all registered job/operation data, queues, calendar and results.
Growth does not invalidate existing handles. This milestone retains completed state until close; it
does not demonstrate slot recycling, cancellation or bounded memory over an infinite stream.

The subsequent [registration experiment](../../specs/research/2026-11-01-native-registration-plan.md)
adds native-only `add_many(jobs: tuple[Job, ...]) -> list[JobHandle]`. The tuple and every Job must be
exact types. It validates the whole Python batch before native mutation; rejected input leaves the
stream usable. Empty tuples return an empty list but still require an accepting stream. C copies all
inputs before returning, assigns contiguous IDs, and schedules every individual arrival in scalar
registration order. It does not advance time. C allocation/admission failure may leave partial internal
copies and poisons the stream; only close remains valid. C validates explicit array lengths before
dereferencing and offsets before pointer arithmetic. Scalar add and the event loop are unchanged.

Registering future arrivals ahead of time is optional producer lookahead. It preserves final event
order and records for independent predetermined inputs, but future handles become visible to Python
earlier. It cannot replace per-job feedback-dependent registration without additional semantic work.

The [compiled scalar experiment](../../specs/research/2026-11-01-native-compiled-plan.md) adds opt-in
`CompiledStream`, implementing the scalar interface above without `add_many`. Its Python C API extension
links the same unchanged engine shared library. A thin Python wrapper retains exact Job validation,
owner/id JobHandles and per-stream RLock serialization. C converts primitive route/duration tuples into
temporary copied buffers and owns independent counts/lifecycle state in an opaque capsule. Native calls
release the GIL; a busy guard rejects concurrent/reentrant direct capsule calls, including close, and
protects detached result copying. Public methods serialize through the RLock as in ctypes.

Python-invalid input is rejected before engine mutation and leaves the stream usable. Native allocation,
time/recording failure poisons the stream, with close still valid. Capsule destruction and close are
idempotent with respect to engine ownership. Results and inspections contain detached primitives/lists.
This is a GIL-enabled, local Python C API prototype measured on CPython and PyPy's cpyext compatibility
path, not a stable ABI, free-threading, subinterpreter, wheel or CFFI certification.

`inspect` returns `id`, `arrival`, `released`, `finished`, `state`, with None for unavailable times and
state one of `scheduled`, `pool`, `active`, `finished`. Inspections are detached snapshots. No mutable
native storage is exposed. Allocation failure fails the stream safely; close remains valid.

## Event and release semantics

Event order is `(time, arrival_priority, insertion_sequence)`, arrival priority 0 and other events 1.
Every registered job has one arrival event. Equal-time arrivals occur in registration order before
other events at that time; registering later batches must preserve the same result as eager registration.
Each arrival is announced, enters the pre-shop pool, and releases immediately if there is floor capacity.
This mirrors the production ConWIP arrival callback, rather than selecting EDD among future arrivals.

Release reserves a floor slot synchronously and enqueues the first operation. Processing uses the
static prototype's START/END/ADVANCE/RELEASE_DISPATCH sequence; grant reserves a server before START.
FIFO uses enqueue order; SPT breaks duration ties by enqueue order. On final ADVANCE, the source server
is freed and its dispatch callback scheduled, FINISH is announced, the floor slot is freed, then pending
jobs release until the cap is full. Choose pending jobs by (due date, arrival/registration order).
Immediate release (`wip_cap=None`) uses the same machinery without a cap. Cap accounting works in every
observation mode, including none. There are no Python dispatch hooks in this initial dynamic slice;
the separate hook experiment informs the next extension interface without coupling these changes.

## Results, KPIs and records

Result keys match the static prototype, plus `released`, a float list by job ID. Trajectory fields
remain detached Python lists. Diagnostic events may be backend-specific; callbacks is zero.
Makespan is max(finished, default=0), even when advance moves the clock beyond the final completion.
KPI fields retain system arrival-to-finish flow/WIP, queue-to-start wait, tardiness and server busy time.
Add `total_pool_wait`, `floor_wip_area`, `max_floor_wip`, where floor WIP changes only on RELEASE/FINISH.
The integral of floor WIP equals sum(finish-release), and system WIP equals sum(finish-arrival).
Every mode retains exact trajectories and releases. Aggregate overflow may yield infinity; event times
must remain finite and advance.

Record header is `NDYN0001`, followed by the existing fixed little-endian 32-byte `<dQIIII` records.
Kinds are 1 ARRIVAL, 2 QUEUE, 3 START, 4 END, 5 FINISH, 6 RELEASE. ARRIVAL/RELEASE/FINISH use sentinel
operation/server IDs. Records flush before successful run, without fsync. Header/kind changes are
explicit; this is not the static prototype format or production Studio tracing.

## Required evidence

- Hand-derived cap/EDD cases, arrival/completion ties, temporary draining followed by new jobs, empty
  input, safe handles after growth/close, invalid times/counts/jobs, overflow and lifecycle errors.
- Exact native/reference trajectories, releases and record bytes on randomized workloads and feeding
  partitions; independent job/resource/cap/conservation/KPI and recording replay checks.
- Standalone ASan/UBSan including growth/ownership and allocation faults; old static suites stay green.
- Serial fresh-process comparisons on CPython and PyPy with equivalent streamed registration, including
  construction, registration/advance/run, detached extraction and close/GC. Include cold/RSS separately.
  Preserve source/library/input hashes, commands, raw samples and uncertainty. No hidden registration
  costs or comparison of ConWIP results to an immediate-release baseline as equivalent work.

## Production trace projection gate

The [trace study](../../specs/research/2026-11-01-native-studio-trace-plan.md) adds opt-in source mode
studio. Raw NTRC0001 keeps 32-byte framing, adding REGISTER(7), GRANT(8), DEPART(9) at actual admission,
server reservation and departure execution points. Source records count 4 per job plus 5 per operation.
Existing modes keep their behavior. Rich recording accepts future/bulk jobs; full TraceStream projection
is limited to scalar registration at arrival, matching the two measured producers.

TraceStream projects available complete records into actual production kinds/events through the unchanged
TraceRecorder. A separate empty-calendar Environment consumes validated source times without running
model processes or feeding state back. Final SIMTRACE 1.3 includes snapshots/deltas/digest/CRC/zlib/index/
footer/manifest/KPIs. Source queue order and actual SPT removals are represented, not a claim of identical
micro-event ordering to SimPy's scheduler. Intermediate I/O, shadow state and complete writer close are
timed. Record buffering can defer projection; completed-file compatibility is not bounded live delivery
or managed Studio integration. Latency sealing is disabled; other production limits remain unchanged.


## Native stock scalars (opt-in research path)

`NativeKPIStream` keeps the eight `ShopFloorKPIs` in C: makespan, lateness,
tardiness, tardy fraction, total queue time, throughput, utilization and jobs in
system. Here makespan is mean creation-to-completion time and jobs in system
counts the shop floor, excluding the pool. Warm-up applies to the completion
cohort and clips busy/floor signals independently. Unfinished jobs are excluded
from job means but contribute to time-weighted signals. Empty values are omitted;
non-finite scalar values follow the production collector.

Snapshots are detached, read-only queries between external calls. An advance to
`t` processes events strictly before `t` and returns a right-open window; a run
to exhaustion closes it, including an already-advanced idle tail. Repeating a
snapshot does not split integral intervals or change later results. Close frees
state without executing pending events. A failed mutation poisons the stream;
a temporary snapshot allocation failure leaves it usable.

This path configures before jobs/time advancement and fixes the shop floor and
unit server capacities for the run. It has no Python event publication, journal,
trace, per-event callbacks, history or EMA. The engine still retains trajectory
arrays; this is not a bounded-memory engine claim. The legacy observer path is
separate. It does not permit arbitrary event-time inspection of native state.


## Managed scalar execution research adapter

The research model accepts only a fresh managed Horizon environment, fixed unit
server capacities, FIFO/SPT, EDD ConWIP and independent/feedback arrival input.
Warm-up and right-open horizons match the managed stopping policy. The native
stream advances immediately before each outer step, including the stop marker;
scalar reads never execute model work. The adapter installs its step wrapper at
activation, after the build guard, and restores lifecycle methods on close.
Managed ready/close/error handling remains unchanged. Close releases backend
state even when scalar export fails.

A scalar-only Collector exposes the eight existing estimands to the unchanged
KPI result file and result store. The shell's semantic state and event counter
do not describe native actors; full domain tracing/replay are rejected. This is
a private integration experiment, not a general Environment implementation.
Source capture includes relocatable native binary bytes and their integrity
manifest, so library changes change request identity and cannot silently reuse
old results. The model checks those hashes before loading the native runtime.
