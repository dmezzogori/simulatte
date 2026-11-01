# Direct production baseline and profiling

2026-11-01; starting revision 044cfbf, feature/native-engine-feasibility.
Davide authorized the [bounded experiment](2026-11-01-native-production-plan.md)
after clarifying that the [previous trace study](2026-11-01-native-studio-trace.md)
compared all engines through the same projection bridge.

## Results

**The direct production baseline strengthens the case for native without recording,
but makes the full-trace comparison less favorable.** At 5k jobs, native CPython is
23.36x/6.12x faster than production PyPy without recording (independent/feedback).
With full recording it takes 1.53x/1.50x as long as production PyPy. Against the
same CPython interpreter its full-trace gain is only 1.01x/1.04x. Keep the native
prototype experimental; these results do not justify broad traced integration.

Complete lifecycle medians, milliseconds; compare within each producer. The two
producers generate different operation counts and therefore different workloads.

| Producer | Recording | Production CPython | Native CPython | Production PyPy | Native PyPy |
|---|---|---:|---:|---:|---:|
| Independent | none |405.17|7.24|169.07|23.60|
| Independent | KPI |1,486.53|1,129.50|941.79|1,123.28|
| Independent | full |2,126.35|2,104.58|1,371.56|1,910.72|
| Feedback | none |271.83|20.63|126.23|36.69|
| Feedback | KPI |1,173.73|922.06|768.78|948.98|
| Feedback | full |1,654.98|1,596.50|1,064.63|1,481.06|

KPI recording gives native CPython a 1.32x/1.27x advantage over production CPython,
but production PyPy is still 1.20x as fast as native CPython. Full native PyPy is
also slower than production PyPy, by 1.39x in both producers. The previous compact
controls remain useful for isolating kernel choices under a common bridge;
they were a weaker traced alternative than actual direct production PyPy.

All 24 pre-measurement gate cases pass exact trajectories/generation/feedback,
KPI tolerance and ordered WIP-series equivalence. The repeated matrix and cold
probes preserve these results. This establishes equivalence for the measured
model and fixtures, not arbitrary SimPy/Python extension semantics.

## File sizes, cold lifecycle and memory

Independent/feedback workloads contain 27,363/20,612 operations. Production full
files contain 226,541/179,284 domain events versus native 216,541/169,284: production
records 10,000 extra policy/retirement events. Despite that, its files are smaller:
**10,359,047/7,458,873 bytes** versus native **16,659,629/11,928,105 bytes** on CPython.
Production has 32/26 chunks versus 31/25 native. PyPy files are two bytes smaller
because of interpreter metadata. KPI files are about 0.89 MB with no domain chunks.
Native's intermediate journals add 5,018,088/3,937,928 bytes of local I/O.

Single cold full-lifecycle probes, milliseconds:

| Producer | Production CPython | Native CPython | Production PyPy | Native PyPy |
|---|---:|---:|---:|---:|
| Independent |2,141.35|2,095.39|2,612.61|2,658.80|
| Feedback |1,663.44|1,585.02|2,415.47|2,216.65|

PyPy's warm benefit does not carry over to these one-shot cold probes. CPython
production already comes close to native with full recording; comparing native
only against cold PyPy would give a misleading impression of its benefit.
Cold measurements exclude startup/imports/parsing, as do the repeated samples.

Cold whole-process peak RSS for full traces, MiB: production CPython 85/79,
native CPython 120/94; production PyPy 178/142, native PyPy 198/188. The benchmark
harness imports both implementations/libraries and retains input templates and
exported results. These are not production deployment memory estimates or
isolated native-engine allocation measurements. The largest sample range spread
in the matrix is 7.02% (PyPy independent production without recording).

Full CPython production ranges are 2,114.40–2,137.39 ms independent and
1,650.43–1,661.15 ms feedback; native 2,100.91–2,119.73/1,585.99–1,612.43 ms.
The independent ranges overlap. These small same-interpreter gains should not
be confused with the large no-recording native advantage. Full production PyPy
ranges are 1,366.81–1,383.67/1,059.76–1,075.78 ms, clearly below either native path.

## What the profiles explain

The bridge performs an additional Python state replay on the simulation thread:
211,541/164,284 calls to apply_deltas independent/feedback. Direct production
does not perform this replay; its model already owns the live state. Both paths
also replay deltas in the writer to produce snapshots. The bridge adds journal
decoding, per-record validation, shadow entities and event construction around
the same shared event/digest/encoding path. Profiles identify all of these;
they do not establish that the native kernel itself has become slow.

Retaining completed shadow jobs inflates repeated snapshots. The separate
output-span pass measures the actual snapshots and output calls:

| Producer/backend | Snapshot bytes across all chunks | Max jobs in a snapshot | Snapshot-pack CPU (ms) | Compression CPU (ms) | File-output CPU (ms) |
|---|---:|---:|---:|---:|---:|
| Independent production |5,584,288|1,279|59.68|423.28|3.24|
| Independent native |25,841,764|5,000|293.32|655.00|3.17|
| Feedback production |211,547|50|2.81|285.56|2.51|
| Feedback native |15,671,045|4,967|193.75|455.76|2.93|

These are aggregate uncompressed snapshot bytes, not the whole file or a single
snapshot. Native writes 4.63x/74.08x as many snapshot bytes. Compression includes
the entire event+snapshot chunk, so its difference cannot be attributed solely
to snapshots. Native's last snapshot need not include the last registered jobs;
its final replay state still retains all 5,000.

File-output CPU covers write/commit/close, not the source journal. Output wall
spans are much larger: production 573/466 ms and native 88/20 ms independent/feedback.
Compression wall spans also exceed their CPU time. A writer can wait for the
simulation thread, GIL, OS scheduling or I/O; these numbers do not isolate slow
storage. Median whole-process CPU exceeds wall time in full cases (for example,
CPython independent production 2.43 CPU-s/2.13 wall-s and native 2.54/2.10), consistent
with substantial CPU work overlapping between threads. Do not add these spans
to simulation wall time or claim that replacing disk writes would remove them.

The function profiles show shared digest handling, canonical wire preparation,
event construction and writer replay among the hot work. Native additionally
shows the journal/shadow replay above; production also pays real SimPy/resource
and ConWIP selection work. The Python profiler perturbs call-heavy code greatly
(for example, the no-recording production profile reports 4.22 CPU-s versus a
0.405 s unprofiled lifecycle). Its absolute seconds and cross-backend percentages
are not speed estimates. Call counts and the separate snapshot/output-span pass
provide the stronger evidence for the proposed experiment.

## Recommended next experiment

First align the native projection's lifecycle with production: record release
policy decisions and retire completed shadow entities from trace replay state,
while preserving native results, completed-handle inspection and trajectory
semantics. This directly targets measured snapshot growth and makes the two
observed lifecycles more comparable. Re-run the same correctness gate and
24-case matrix; measure the actual effect rather than subtracting profile spans
from the current wall time.

If a useful traced margin still does not appear, evaluate one bounded change
that avoids the intermediate journal/shadow replay, or a shared digest/wire
optimization justified by a focused profile. A full C trace serializer is not
the immediate next step. Keep the strong no-recording result as a separate
optional-backend opportunity, subject to extension/semantic/packaging gates.
These are recommendations, not work implemented or newly adopted commitments.

Follow-up: the [lifecycle experiment](2026-11-01-native-lifecycle.md) tests the
first recommendation while preserving this study's source/evidence snapshot.

## Verification and retained evidence

265 prototype tests pass CPython (one sanitizer-only skip);264 pass PyPy
(sanitizer and CPython-only profiling skips, two known missing pytest-timeout
configuration warnings).156 existing production trace reader/writer/sink,
ConWIP and observer-invariance tests pass. The five targeted tests and Ruff
pass after the final profiling adjustments. No native C/extension, production
runtime, TypeScript, dependencies, CI or original fixtures were changed.

The audit passes 24 cases/120 timed samples/24 cold probes, exact case order,
source/binary/input provenance, trajectories/generation/feedback, KPI tolerance,
WIP series, within-backend digest equivalence and ten corrupt-evidence rejection
checks. All 24 retained preflight rows separately match the matrix's output
signatures/counters. Twelve CPython profile files cover 12 simulation and 8 writer
threads, with 8 separate output-span passes. This iteration does not re-certify
native sanitizers or browser compatibility; those artifacts are unchanged from
the previous verified gate.

Evidence: [matrix](../../prototypes/native_engine_dynamic/production-results/matrix.jsonl),
[summary](../../prototypes/native_engine_dynamic/production-results/summary.csv),
[metadata](../../prototypes/native_engine_dynamic/production-results/metadata.json),
[preflight](../../prototypes/native_engine_dynamic/production-results/preflight.json),
[profile summary](../../prototypes/native_engine_dynamic/production-results/profile-summary.json),
[audit](../../prototypes/native_engine_dynamic/production-results/verification.json).
Function-level profiles are retained in the adjacent profiles directory. Large
performance traces are validated and deleted; raw sample/hash/count/size evidence
is retained. Frozen source SHA-256:
ca98256accb052511bc57c5e12f5d529fb4a87ee5d77d4b2617f754518472a30.
Engine/static/CPython/PyPy extension hashes remain unchanged from 044cfbf and are
recorded in metadata. Environment: bigboss, CPython 3.14.3/PyPy 3.11.15(7.3.22),
Simulatte 0.17.0, SimPy 4.1.2, msgpack 1.2.3; full platform/versions are in the evidence.

## Comparison contract

The new baseline uses the actual production Environment, ProductionJob, Server,
ShopFloor, PreShopPool and ConWIP, without changes to production sources. SPT,
cap 12, ten servers, the historical 5,000-job load-.95 templates and the shared
independent/live-feedback scalar producer match the native study. Two public
synchronous operation hooks export the start/end arrays and dispatch order
returned by the native API. They do not control scheduling or release. This is
a production model with comparable trajectory export, not the cost of every
possible ordinary Simulatte model. Default EMA metrics are disabled on the floor.

The custom collector exports the same ten native-total scalar KPIs and system/
floor WIP series. Production derives scalar exports from its completed trajectory
inside the timed lifecycle; native accumulates them during simulation. These are
not all the standard production collectors' estimands. No recording creates no
recorder/collector; KPI/full modes use the real TraceRecorder at the respective
level. **KPI level still enables the semantic digest and constructs domain events.**
It does not mean scalar counters without observation work. Native KPI/full both
retain the rich journal and Python projection bridge.

Production emits policy.decision and entity.retired: two more domain events per
job than the bridge. Its WIP initial samples are prelude; the bridge publishes
them after activation. Production retires finished jobs from replay state;
the bridge retains them. Digest/file equality across these two event models is
therefore inappropriate. Compare exact job generation, feedback decisions,
operation/release/finish arrays and global dispatch order; compare scalar KPIs
within rel_tol 2e-11/abs_tol 1e-8 and ordered WIP (time,value) series exactly. Check
each full trace's own reconstruction, all operation/release/finish times, final
resource/pool/floor state and digest. KPI files pass container/CRC checks but
their digest cannot be independently replay-verified without domain chunks.

## Measurement protocol

24 cases: CPython/PyPy x independent/feedback x none/KPI/full x production/native.
Five timed samples after one CPython/seven PyPy warmups, plus separate cold/RSS
probes; serial case order shuffled with seed 20261104. Imports, DSO loading and
input parsing are excluded, including cold probes. Construction, job conversion,
dynamic generation/feedback/logs, all engine/projection/trace work, aggregate and
trajectory export, source/trace I/O, complete writer shutdown and GC are included.
Post-run validation is excluded. RSS is a fresh-process whole-process peak
captured before validation, not isolated engine memory. Cold probes are single
observations, not distributions. Writes use the page cache, without fsync.

Default count/byte/pending writer limits (10,000 events, 1 MiB, 64 MiB), infinite
latency for deterministic chunking. Every repetition checks CRCs, normalized
deterministic wire content, trajectories, generated inputs, decisions, stored
digest/KPIs and WIP samples; first/cold runs also reconstruct/check full traces
and rich source journals. Within each backend the digest is invariant across
recording levels and interpreters. The baseline and bridge digests differ by
design. Do not extrapolate this finite study to general SimPy semantics,
arbitrary hooks, bounded infinite streams or a direct native encoder ceiling.

## Profiling method

Profiling runs are separate CPython diagnostics, not performance samples or PyPy
JIT evidence. Use the standard Python profiler with time.thread_time, profiling
the simulation and writer in separate identical passes. Finalize each profile
immediately on the same thread. An initial cProfile pilot on installed CPython
3.14.3 observed other threads and gave invalid thread-clock accounting; no such
pilot data enters the retained evidence. A second pilot caught delayed profile
finalization charging unrelated work to setprofile; same-thread immediate
finalization corrected it. Tests check thread coverage and CPU sanity.

Function self CPU is exclusive; cumulative times overlap and must not be added.
Instrumentation changes call costs and scheduling, so profile proportions are
diagnostic, not a causal decomposition of the unprofiled wall time. A third
unprofiled diagnostic pass records wall/CPU spans for the actual snapshot pack,
compression and FileSink write/flush/close calls, plus snapshot byte/entity/job
counts. These spans include scheduling/page-cache effects, exclude fsync and
exclude the C journal's internal writes. Journal decoding/projection are covered
in the simulation profile. All diagnostic passes check trajectory equivalence.

## Reproduction

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.production_benchmark matrix \
  --output prototypes/native_engine_dynamic/production-results \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python
# For each producer/backend/mode, run profile with the same input and 5k jobs:
.venv/bin/python -m prototypes.native_engine_dynamic.production_benchmark profile \
  --input prototypes/native_engine/.inputs/jobs-5000-load-0.95.json \
  --backend native --producer independent --mode full \
  --output prototypes/native_engine_dynamic/production-results/profiles/independent-full-native.json
.venv/bin/python specs/research/native-engine-data/production_analysis.py \
  prototypes/native_engine_dynamic/production-results
```

Sources, production/prototype Python, compatibility artifacts, new tests/audit,
locks, engine/extension binaries and input hashes are frozen. Earlier studies
require their recorded checkout/binaries; their evidence has not been rewritten.
