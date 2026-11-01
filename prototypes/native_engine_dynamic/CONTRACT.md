# Dynamic-job and ConWIP research contract

Davide authorized the hook-boundary experiment followed by dynamic jobs/ConWIP on 2026-11-01.
This is an isolated research milestone. Production runtime, existing static C engine/ABI and historical
evidence remain unchanged. Production Studio encoding is the subsequent separate gate, not part of this
milestone. Design choices below are implementation decisions within that bounded authorization.

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
