# Native production trace compatibility and complete lifecycle cost

2026-11-01, starting revision 0f20924, feature/native-engine-feasibility.
Davide authorized “Push, then proceed” after the [compiled scalar study](2026-11-01-native-compiled.md).
Pushed all four completed research commits through 0f20924 before starting this
[bounded trace gate](2026-11-01-native-studio-trace-plan.md).

## Outcome and architecture decision

**Compatibility passes; this bridge does not justify a general native backend on full-trace performance.**
At 5,000 jobs, native CPython takes **2,134.63 ms independent** and **1,579.52 ms feedback**, versus the
faster compact PyPy control **1,825.02/1,478.30 ms**: native throughput is **0.85x/0.94x** that alternative.
It gains only **1.02x/1.03x** over compact CPython. Full projection/encoding costs absorb the useful
kernel margin. Native on PyPy is also near parity with compact PyPy at 5k (1.00x/.98x).

Without observers, native CPython retains **4.82x/1.86x** compact PyPy throughput; rich raw recording
retains **5.21x/2.30x**. Preserve those results and keep the prototype experimental. Stop kernel, binding
and batch tuning here. Compact CPython/PyPy remains the simpler measured choice for traced workloads.
Further integration needs a different trace architecture or a concrete product need that justifies it.
This single bridge does not set a ceiling for direct native encoding, and no general rewrite or direct
native writer was started.

## Complete lifecycle measurements

5k jobs, medians in ms. Each compact column selects the faster fresh reference/tuned control under the
same producer/mode. Ratios are compact PyPy time / native CPython time; above 1 means native faster.

| Producer | Observations | Native CPython | Best compact CPython | Best compact PyPy | Native throughput ratio |
|---|---|---:|---:|---:|---:|
| Independent | none | 7.27 | 55.08 | 35.10 | 4.82x |
| Independent | rich raw | 9.31 | 83.42 | 48.48 | 5.21x |
| Independent | full trace | 2,134.63 | 2,169.56 | 1,825.02 | 0.85x |
| Feedback | none | 20.37 | 57.32 | 37.80 | 1.86x |
| Feedback | rich raw | 21.77 | 79.54 | 49.98 | 2.30x |
| Feedback | full trace | 1,579.52 | 1,630.15 | 1,478.30 | 0.94x |

Native PyPy full medians are 1,832.63/1,510.88 ms independent/feedback. At 500 jobs, full native CPython
is 168.16/154.29 ms versus compact PyPy 170.41/158.70 ms (1.01x/1.03x). Native PyPy is 152.93/157.24 ms
(1.11x/1.01x versus compact PyPy). These modest small-case differences do not establish general scaling.
Small raw/none cases are noisy and can have non-monotone medians; do not infer negative recording cost.

At 5k, full native CPython is 229x/73x its raw lifecycle and 293x/78x its no-observer lifecycle. This
compares complete variants, not isolated serialization. Independent phase medians are build 1.42,
feed 1,332.63, drain 416.37, extract .63, close 388.25 ms; feedback 1.42/1,403.04/20.83/.43/153.10 ms.
Close includes the outstanding writer's completion. Phase medians need not sum to the total median;
feed/drain contain both native work and projection/encoding.

## Sizes, ranges, cold lifecycle and memory

5k independent: 27,363 operations, 216,541 domain events, 20,002 observers, 31 chunks. Feedback:
20,612 operations, 169,284 domain events, 20,002 observers, 25 chunks. CPython full files are
16,659,511/11,927,984 bytes; PyPy files two bytes smaller, with identical semantic signatures.
Source journals are 5,018,088/3,937,928 bytes. At 500, full files are 890,072/798,478 bytes, three chunks
each. Interpreter metadata differs; physical file identity is not required for semantic equivalence.

5k CPython native full ranges are 2,109.20–2,148.45 ms independent and 1,568.67–1,600.95 ms feedback,
spreads 1.86%/2.06%. PyPy native ranges 1,814.69–1,842.03 and 1,501.35–1,535.47 ms, spreads 1.51%/2.27%.
Largest full-case spread is 9.42% (500-job PyPy reference independent); largest overall is 133.59%
(500-job PyPy reference feedback/raw). Small/JIT-sensitive cases deserve particular caution.

Cold native CPython is 2,146.85/1,598.29 ms and native PyPy 2,535.78/2,297.50 ms. Cold compact CPython
is 2,180.96/1,636.21 ms; compact PyPy 2,628.90/2,473.74 ms, selecting the same controls as the warm table.
These are single fresh-process probes, not distributions, and exclude startup/imports/parsing. Looking
only at cold PyPy would overstate the native benefit: compact CPython is already close for one-shot runs.

Cold peak RSS for full native CPython is 116/92 MiB, native PyPy 214/173 MiB; the selected compact
CPython controls use 117/101 MiB and PyPy 184/164 MiB. These are whole-process peaks including templates,
generated inputs/logs, shadow entities, queued writer work, snapshots and output, not isolated engine
allocations. Production KPI-only encoding and larger full traces were not evaluated.

## Trace implementation and compatibility

The prototype now produces real production SIMTRACE 1.3 through the unchanged TraceRecorder: builtin
job/server/psp/shopfloor kinds, typed events and immutable deltas, dynamic creation, queue/users/work,
pool/floor WIP, operation timings, KPI declarations/series/scalars, provenance, zlib chunks, CRCs,
start snapshots, index/footer and semantic digest.

This is a sidecar bridge, not a direct native MessagePack encoder. Old NDYN records omit actual grant
and departure markers; inferring them at START/END would reconstruct incorrect intermediate state.
Opt-in NTRC0001 source recording adds REGISTER/GRANT/DEPART at actual C/reference execution points,
preserving equal-time order. These conditional observations change no scheduling/dispatch/ConWIP
decisions, and existing modes retain their behavior. Both native interfaces link the same newly
instrumented DSO. Previous measured binaries were archived before rebuilding; old evidence is untouched.

After scalar boundaries, a projection-only Environment consumes complete available source records at
their validated times in an empty calendar. It emits production events through the actual bus/digest/
writer, without model processes or feedback to the engine. Builtin schemas are inherited without
constructing SimPy model actors. Shadow state and the writer's replay state apply immutable deltas.
Intermediate I/O, projection validation/replay and full writer shutdown remain timed.

Full projection accepts scalar registration at arrival. Rich raw mode retains future/bulk admission.
Native queue order and actual SPT removals are represented; compatibility does not mean identical
micro-event ordering to SimPy PriorityResource/scheduling. Buffering can defer projection until later
boundaries. This proves completed-file compatibility, not bounded live delivery or managed Studio
execution integration. Custom native KPI totals/series are declared; not every production collector
estimand is covered. The bridge uses a separate Environment clock, without changing production APIs.

## Verification

260 prototype tests pass on each interpreter, one sanitizer-only skip each and two known missing-timeout
warnings on PyPy. Rich source bytes/results match native/compiled/reference/tuned. Hand cases inspect
queue/users before/after real grants and departures at equal-time cursors. Dynamic entities, multiple
snapshots, KPIs, empty input, invalid calls, cancelled/failed outcomes, corruption and writer errors pass.
134 existing production trace reader/writer/sink tests pass, including their recovery checks.

Engine ASan/UBSan passes 1,025-job exercises with 143 scalar, 143 rich-record and 144 bulk allocation-fault
positions. The compiled boundary's separate CPython ASan/UBSan suite passes 31 cases with three state/
scratch fault positions; leak detection disabled. This is not a PyPy-runtime sanitizer claim.

Retained [native fixture](../../prototypes/native_engine_dynamic/fixtures/native.simtrace),
[source](../../prototypes/native_engine_dynamic/fixtures/native.source) and
[expected states/history](../../prototypes/native_engine_dynamic/fixtures/native.expected.json): 12 jobs,
228 domain events, 50 observers and 14 chunks. Python Trace.check(), verify() and state_at() pass. Digest:
540b482c67af594f67c8ab340be0a9484a21d59bc6c09504f11dfdcbbca4ec02.

The actual TypeScript reader/playback/scene checks full history, selected exact states/KPIs, seeks both
ways and scene placement against Python. Suites pass 301 trace, 25 playback and 16 scene tests; all three
typechecks and the production viewer/player build pass. Browser smoke opens, steps, seeks, plays and
renders in Chromium, Firefox and WebKit. Python provides digest/snapshot verification: check()/verify()
are not ported to TypeScript. No production runtime/writer/reader/viewer source changes were needed;
these core files also match recorded origin/main ea1292c. Reviewed inline, no separate agent reviewer.

## Measurement protocol

72 serial shuffled cases: 500/5000 prefixes of historical CPython-generated .95 load templates,
independent/feedback, none/rich raw/full trace, compiled native/reference/tuned, CPython 3.14.3 and PyPy
3.11.15/7.3.22. ConWIP cap 12, SPT. Feedback changes jobs after live inspection; compare within a producer.
Generated inputs, decisions, trajectories and rich source bytes must match all equivalent cases.

Five samples, CPython 1/PyPy 7 warmups, separate cold/RSS, seed 20261103. Bigboss M4 Pro 12 cores, 48 GiB,
macOS 27.2. Parsing/template validation/imports/library loads are outside timing. Engine/trace construction,
copies, handles, feedback/Job creation/logs, journal I/O, projection validation/deltas, digest, encoding,
compression, detached results and all close/GC/writer completion are inside. Correctness validation is
outside; cold process RSS is captured beforehand and includes the whole process, not just engine memory.

Production 10k-event/1MiB/64MiB pending limits are retained; latency is infinite for deterministic chunks.
Flush is to OS buffers, no fsync. Raw mode includes KPIs and richer observation work, so raw-minus-none
is not isolated I/O cost. Full-minus-raw includes shadow replay and asynchronous pipeline effects; phase
medians are not an additive decomposition of serializer/kernel cost.

All invoked Python, relevant TypeScript, fixture/test bytes, lockfiles, engine/two extensions and input
are frozen. Imported writer paths are checked against this checkout. Every full repetition checks all
CRCs and normalized deterministic wire bytes, excluding volatile header/footer metadata and offsets.
Within-case hashes match including cold. First/cold additionally fully verify snapshots/digest and
events/state/KPIs. Cross-interpreter comparisons use semantic signatures, not volatile/compressed bytes.

[Raw matrix](../../prototypes/native_engine_dynamic/trace-results/matrix.jsonl),
[metadata/source snapshot](../../prototypes/native_engine_dynamic/trace-results/metadata.json),
[CSV](../../prototypes/native_engine_dynamic/trace-results/summary.csv),
[audit](../../prototypes/native_engine_dynamic/trace-results/verification.json),
[reproduction commands](../../prototypes/native_engine_dynamic/README.md#production-studio-trace-gate).
The audit requires matching source/library/input snapshots, checks coverage, identities, timing sums,
counters/sizes, software/provenance and equivalence, and rejects nine corrupted-evidence variants.

One candidate, one host, moderate inputs, five samples and seven PyPy warmups do not prove steady state
or confidence intervals. Do not extrapolate to 50k traces. Retained completed jobs/full snapshots grow
with jobs. This bridge is not a ceiling for direct native encoding. General extension semantics,
cancellation/recycling, bounded infinite streams and packaging remain separate. No broad production
backend integration or kernel/binding tuning is included.

Frozen source snapshot: 072e8237a6560b82efa43924884f095fd8f94dd4abe579681a15f46e19286415.
Engine DSO: 46a60b463fbb61689a72bbccadb665710b6273ededfd721ecdcedba7438148ba; both extension hashes
and input/software versions are retained in metadata. Both interpreters use simulatte 0.17.0,
SimPy 4.1.2, msgpack 1.2.3 and zlib 1.2.12. The complete audit passes 72 cases/360 samples/72 cold probes,
exact equivalence and all nine corrupt-evidence checks.
