# SP1 Gate G1 report: vertical slice

Branch `feature/sp1-events-trace`, Tasks 1–10. Spec: `specs/2026-10-08-sp1-events-trace-design.md` (rev 4), §2 G1 row.
Measured on eva (MacBook Pro 2021, Apple M1 Pro, 16 GB), CPython 3.14.1, 2026-10-09.

**Verdict: G1 acceptance passes.** Every criterion of the G1 row holds in `tests/core/test_g1_acceptance.py::test_g1_acceptance`, and the full suite passes with the coverage gate (1191 tests, total coverage 99.48 %, `src/simulatte/trace/reader.py` 100 %).

## 1. Acceptance criteria

The reference shop (`build_reference_shop` in the test module) is a 4-machine LumsCor job shop (`Scenario(n_servers=4)`, `check_timeout=5.0`, `wl_norm_level=6.0`, `allowance_factor=2`, seed 20260508, horizon 300) plus a `lathe` `Server` used directly by a process without a `ShopFloor`. Every 7 time units the process creates 1 to 3 jobs that request the lathe at the same time; the lathe is idle at the start of each round, so the first request is granted inside its constructor and the others queue.

| Criterion (spec §2, G1) | Evidence |
|---|---|
| A reference job shop records a `full` trace | The trace has 47 chunks (`ChunkLimits(max_events=200)`), outcome `completed`, not truncated; `Trace.check()` passes (all CRCs, every chunk decoded, INDEX records equal the footer index, every chunk snapshot equals the replay of the earlier chunks). The `(t, seq)` cursors of `trace.events()` equal those of the live domain events, in order (payload and delta fidelity is covered by `verify()` below and by the seek checks). |
| Python seek equals uninterrupted replay at every chunk boundary | `state_at(first)` and `state_at(last)` of all 47 chunks equal the state obtained by applying, in one pass, the deltas of the live events (collected by a bus subscriber during the run, independent of the file) to `env.initial_state`. The activation cursor `(0.0, -1)` returns the initial state. |
| ... and at sampled intermediate cursors | 150 random event cursors, 100 random cursors followed by an event at the same time (the run has more than 1000 such pairs), and 50 cursors between two events (midpoint times, seq 0) match the replay. The replay after the last event equals the live `env.entities.snapshot()`, and so does `state_at` at the end of `cursor_range`. |
| ... including direct `Server` use and immediate grants | All lathe `job.granted` events that directly follow their own `job.queued` with `queue_length == 1` (43 immediate grants; 85 lathe requests in total) are seek targets and match the replay. |
| Digest identical across observer configurations | Five configurations give the same digest `9032ec34…29c51f`: recorder with small chunks plus a live-log subscriber (the seek run), digest only, digest plus a `"**"` subscriber (log/observer events included) and a second `"*"` subscriber, `kpi` recorder, `full` recorder with default chunk limits. |
| Digest identical across `PYTHONHASHSEED` values | Fresh `sys.executable` subprocesses with `{**os.environ, "PYTHONHASHSEED": s}` for s = 0, 1, 123 print the same digest as the in-process runs. |
| `Trace.verify()` is true | `verify()` recomputes the digest from the trace's initial state, events and stored catalog (presentation fields removed through the trace's own kind schemas and event catalog) and returns `True` for both `full` traces; it returns `"not_verifiable"` for the `kpi` trace. |

Reader unit tests (`tests/core/test_trace_reader.py`, 19 tests) cover the brief's list: boundary and sampled seeks, the activation cursor, a trace with no domain events, incomplete tails (partial trailer, cut footer, cut chunk, cut record header, CRC-failing last record), interior corruption (lazy chunk CRC with a footer, scan-time CRC without one, damaged footer and header, bad magic), an uncommitted chunk, unknown required/optional features and format major, limits (`max_record`, `max_chunk`, `max_depth`, `max_len`, index entry count vs file size), `verify()` for full/kpi/tampered traces, the merged manifest after two runs (seed and versions kept, final stopping policy `horizon 20`, `manifest_final` true; without footer the requested part only), plus catalog extensions found through the footer's epoch offsets, KPI records, damaged chunk contents (bad zlib, unknown event type, deltas that do not apply, malformed events, stale snapshots) and inconsistent footer indexes.

## 2. Trace size, events and chunks

| Run | Jobs | Events | Chunks | File size | Bytes/event | Bytes/job |
|---|---|---|---|---|---|---|
| G1 reference (4 servers + lathe, horizon 300, `max_events=200`) | 525 created after activation (85 lathe jobs) | 9,287 | 47 | 407,680 B | 43.9 | – |
| 4-server LumsCor, horizon 10,500, default limits | 15,355 completed | 323,470 | 48 | 9.96 MB | 30.8 | 648 |
| 10-server LumsCor, horizon 3,000, default limits | 4,950 completed | 194,874 | 31 | 7.29 MB | 37.4 | 1,473 |
| 10-server LumsCor, horizon 30,300, default limits | 49,936 completed | 1,952,455 | 315 | 72.6 MB | 37.2 | 1,453 |

With default limits, chunks are sealed by the 1 MiB uncompressed byte limit at about 6,700 events (min/median/max 5,920/6,746/7,079 on the 4-server run), before the 10,000-event limit; the 1.0 s latency limit did not trigger on these runs (a run with `max_latency_s=3600` gave identical chunks).

## 3. Preliminary G3 data points (not the benchmark)

Measured simply with `time.perf_counter`, one run each, no warm-up control. Seek latency is over 500 random event cursors; "cold" clears the reader's chunk cache (4 chunks) before each seek.

| Run | Wall time `none` | `digest` | `full` trace | `Trace.open` | `verify()` | `check()` | Seek cold p50 / p95 / max |
|---|---|---|---|---|---|---|---|
| 4 servers, 15k jobs | 1.07 s | 4.86 s | 7.67 s | 1.1 ms | 4.87 s | 2.72 s | 45.5 / 72.5 / 84.0 ms |
| 10 servers, 50k jobs | 7.25 s | 31.55 s | 49.98 s | – | – | – | 46.9 / 106.5 / 118.7 ms |

Against the C1.7 hypotheses (10-server job shop over 50,000 jobs, `full` trace under 100 MB, seek p95 under 200 ms on a 2021 laptop): 72.6 MB and 106.5 ms p95, both within target on this first measurement.

Findings for G3:

- **Digest cost.** Enabling the digest alone multiplies wall time by about 4.4 (1.07 → 4.86 s; 7.25 → 31.55 s). Canonical encoding of every event projection in Python dominates; `full` adds the recorder's encoding on top (about 7×). C1.9 leaves the KPI-with-digest budget to be fixed in SP1; this number will drive that discussion.
- **Writer throughput.** In the 50k-job run the writer thread (zlib compression and snapshot encoding, sharing the GIL with the simulation) fell behind and the 64 MiB backpressure bound engaged about 190 times, blocking the simulation 9–24 ms each time. Memory stayed bounded as designed, but each episode logs a warning, which is noisy on long runs.
- **Seek cost** is dominated by decoding a whole ~6,700-event chunk (Python-level map rebuilding in `_wire.unpack`); smaller byte limits or a faster decode path would cut it.

## 4. Deviations and rulings made during G1

Controller rulings recorded in the SDD ledger:

| Ruling | Decision |
|---|---|
| R1 | Task 2 implements debug detection of subscriber scheduling only; detection of subscriber RNG draws comes with `env.rng` in Task 4. |
| R2 | Execution stops after Task 10 for the G1 gate review. |
| R3 | No pushes during execution; commits stay local until the gate report. |
| R4 | `_wire.unpack` rejects map keys that repeat after unescaping (for example `"a"` and `"~a"`) with `ValueError`; implemented in this task (commit 9b69ad4). |
| R5 | Schema/existence validation of lifecycle delta operations and its test moved from Task 2 to Task 3. |
| R6 | Debug-mode `float` fields accept only `float`, not `int`; emitting sites coerce explicitly. |
| R7 | Replay and snapshot state carry the entity kind under the reserved key `"$kind"`; `$` cannot start schema field names. |
| R8 | Job `location` vocabulary and writers fixed in spec §5.2 (`psp:<id>`, `queue:<server>`, `server:<server>`, `transit`, `done`, null). |
| R9 | The projection always encodes event time `t` as float64 (spec §9.1 amended). |
| R10 | An exception during the writer's backpressure wait still queues the sealed item (one-time overshoot of the bound), so an interrupted run yields a consistent `cancelled` trace. |
| R11 | `run(until=<simpy.Event>)` records `{"type": "event"}` and makes the final manifest incomplete (`complete=False`); implemented in this task (commit e34829c). Spec §9.3's definition of `complete` does not mention it yet and needs a one-line amendment. |

Reader decisions taken in Task 10 where the spec is silent or that go slightly beyond the brief's interface:

- `Trace.open` raises `ValueError` (not `TraceCorrupted`) for an unsupported format major or unknown required features; `TraceCorrupted` is reserved for damage, inconsistency and limit violations. Unknown record types (a later minor version) are skipped.
- A record whose header claims a payload above `max_record` raises `TraceCorrupted` even when it is the last record of the file, instead of being treated as an incomplete tail: a writer never produces such a length, so it is classified as corruption (or a limit violation) before the short-record check.
- After a CRC-failing record in a footerless trace, the reader walks every following frame (damaged or not) until one is cut short or exceeds `max_record`; any CRC-valid record found raises `TraceCorrupted`, so adjacent damaged records (one bad block spanning a CHUNK and its INDEX) are not mistaken for a tail.
- `truncated` means the reader ignored damaged bytes at the end. A file that ends cleanly after a chunk without its INDEX is not truncated (the chunk is just invisible); a footer without a complete trailer counts as truncated.
- With a valid trailer the index comes from the footer, chunks are CRC-checked when first loaded, and at open the reader scans only the head (up to the first chunk) and the tail (from the last indexed chunk to the footer), which also confirms the last footer entry against its INDEX record. Without a trailer it scans and CRC-checks every record.
- `cursor_range` is None for a never-activated trace; it ends at the footer cursor for complete `full` traces, otherwise at the last visible chunk (the activation cursor for `kpi` traces). `state_at` raises `ValueError` outside it.
- `events(start, end)` yields events with `start < (t, seq) <= end`, so it leads exactly from `state_at(start)` to `state_at(end)`.
- `verify()` also returns `"not_verifiable"` for a `full` trace without a footer digest. `check()` additionally compares each chunk's snapshot with the replay of earlier chunks.
- `kpis()` returns the scalars of the `KPI` records (Task 20 extends it with series).
- Extra read-only API: `Trace.index` (tuple of `ChunkInfo`), `Trace.level`, `TraceEvent` (named tuple of a recorded event). `digest.project_event_parts` and the `presentation_of` parameter of `project_state` let the reader recompute projections from decoded data with the trace's own catalog and kind schemas.
- A trace from an interrupted run may hold one chunk above `max_sim_window`/`max_bytes` (Task 9 note); the reader does not check chunk sizes against the recorded limits, so it reads such traces.

## 5. Open items for the gate review

- Amend spec §9.3 for R11.
- Decide the digest-mode budget with the 4.4× figure in mind, and whether to rate-limit the backpressure warning.
- Trace reader docs (API page, tutorial) are scheduled with the public surface in Task 24.
