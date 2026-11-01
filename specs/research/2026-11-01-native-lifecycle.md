# Native trace lifecycle alignment

2026-11-01; starting revision 1b234fc, feature/native-engine-feasibility.
Davide authorized this [experiment](2026-11-01-native-lifecycle-plan.md) after the
[direct production baseline](2026-11-01-native-production.md) identified retained
completed shadow jobs and duplicate projection as trace costs.

## Result

**Keep lifecycle alignment: it improves full tracing and removes the snapshot/
file-size inflation, while preserving control behavior and native handles.**
Full native throughput improves 1.14–1.21x across both interpreters/producers.
Direct production PyPy remains faster: the best native (PyPy) full path takes
17.2% longer for independent jobs and 20.2% longer with feedback. Native CPython
takes 27.8%/28.2% longer than production PyPy, versus 53.4%/50.0% previously.
This candidate narrows the gap but does not achieve the broader traced-backend
performance objective.

Full lifecycle medians, milliseconds. Production control uses the same
interpreter as the native row:

| Producer | Interpreter | Native before | Native aligned | Native throughput gain | Production now |
|---|---|---:|---:|---:|---:|
| Independent | CPython |2,104.58|1,732.53|1.21x|2,105.80|
| Feedback | CPython |1,596.50|1,380.72|1.16x|1,676.00|
| Independent | PyPy |1,910.72|1,588.97|1.20x|1,356.15|
| Feedback | PyPy |1,481.06|1,295.34|1.14x|1,077.38|

Native full elapsed time falls 17.7%/13.5% on CPython and 16.8%/12.5% on PyPy.
Unchanged production full controls move between 1.12% faster and 1.27% slower,
substantially less than the native improvements. Against production CPython,
aligned native is now 1.22x/1.21x as fast for full traces. Compare producer cases
separately: independent and feedback generate different operation counts.

No-recording native CPython is 7.32/20.52 ms versus production PyPy 164.06/125.19 ms:
22.4x/6.1x as fast. The underlying source/binary/control paths for these cases
did not change; small differences from the earlier study are measurement drift.

KPI recording is a tradeoff. Native CPython rises from 1,129.50/922.06 ms to
1,165.45/965.60 ms (+3.2%/+4.7%), while production CPython is nearly unchanged.
New lifecycle events still enter the semantic digest, with no repeated snapshot
cost to remove at KPI level. Native PyPy changes by +1.6%/+2.0%, similar to its
production controls' drift. This joint experiment does not isolate the cost of
policy decisions versus retirement. Production PyPy remains the faster KPI
recorder at 960.33/782.75 ms.

## File size, memory and cold probes

Both native and production now record 226,541/179,284 domain events and 32/26
chunks, independent/feedback. Native retains 20,002 simulation observer events;
production has 20,000 plus two prelude samples. Event ordering/payload details
still differ. KPI files have no domain chunks.

Full native CPython files shrink from 16,659,629/11,928,105 to
10,367,812/7,464,392 bytes, 37.8%/37.4% smaller. Production files are
10,359,047/7,458,873 bytes: aligned native is within 0.1% of production size.
PyPy files are two bytes smaller because interpreter metadata differs. The
5,018,088/3,937,928-byte source journals are unchanged.

Cold whole-process peak RSS, MiB:

| Producer | Native CPython before/after | Production CPython now | Native PyPy before/after | Production PyPy now |
|---|---:|---:|---:|---:|
| Independent |120/80|87|198/164|162|
| Feedback |94/62|76|188/147|141|

These are single fresh-process peaks before validation, including the harness,
templates, generated inputs, exported results and both loaded implementations.
They are not isolated engine allocations or deployment forecasts. Controls' RSS
also varies between probes. Input/result/history arrays remain retained; shadow
retirement does not make the complete stream's memory bounded in job count.

Cold native CPython full lifecycle is 1,724.50/1,372.82 ms; production CPython
2,130.23/1,668.12 ms. Native PyPy cold is 2,470.59/2,222.25 ms versus production
2,652.38/2,284.62 ms. PyPy's warm advantage is absent in these single cold probes.
Cold excludes process startup/imports/parsing, like the repeated samples; these
observations do not establish cold-start distributions.

## Mechanism confirmed by separate diagnostics

Aggregate uncompressed snapshot data now nearly matches production:

| Producer | Native before (bytes) | Native aligned (bytes) | Production now (bytes) | Max jobs in native snapshot before/after |
|---|---:|---:|---:|---:|
| Independent |25,841,764|5,584,436|5,584,288|5,000/1,279|
| Feedback |15,671,045|211,727|211,547|4,967/50|

Native snapshot bytes fall 78.4%/98.6%. These totals span all chunk-start snapshots;
they are not file sizes or a single snapshot. Max snapshot populations now equal
production's 1,279/50. Completed native handles remain usable although their
shadow actors are no longer live. Small residual byte differences do not imply
identical micro-event/state representations.

In separate unprofiled CPython output-span passes, native snapshot-pack CPU
falls 293.32→59.61 ms independent and 193.75→2.80 ms feedback. Compression CPU falls
655.00→419.89/455.76→284.91 ms. Current production snapshot-pack CPU is 61.17/2.74 ms
and compression 423.24/284.34 ms. Compression covers both events and snapshots;
the before/after experiment changes policy and retirement events together.
Do not add these CPU deltas to wall time to predict further gains.

Native writer profile self CPU falls 4.83→2.26 s independent and 3.31→1.25 s feedback,
close to production's 2.25/1.26 s. These instrumented seconds describe the profiles,
not the unprofiled writer speed. No file-I/O bottleneck is established: output
call CPU remains only a few milliseconds, while wall spans include waits and
overlap with simulation work.

The simulation side still calls apply_deltas 216,541/169,284 times, in addition to
the writer replay. Direct production has no such simulation-thread replay.
Generic projection, event construction, digest and wire preparation remain in
the profile. The extra 5,000 replay calls versus the previous bridge are empty
ConWIP decision deltas. Retirement uses the registry and removes live projection
state directly; it does not eliminate this existing shadow replay.

The native CPython full ranges are 1,730.22–1,735.93/1,379.44–1,390.53 ms and native
PyPy 1,581.51–1,597.73/1,291.03–1,307.34 ms. Production PyPy ranges
1,346.09–1,360.89/1,076.21–1,088.18 ms remain clearly faster. The largest spread
across any case is 12.47% for the small PyPy-feedback no-recording native case;
do not infer fine differences from that measurement.

## Implication and proposed next step

Retirement fixes the identified snapshot-growth problem and improves the
same-interpreter CPython full-trace comparison. It also removes the large native
file-size penalty and reduces measured memory. The bridge remains experimental:
the strongest production PyPy baseline still wins full and KPI recording.

The next bounded candidate is to avoid the extra Python projection/replay while
preserving the real writer's lifecycle, digest and historical state. First
identify the minimal event/delta data the bridge needs from the native boundary;
then test one direct-emission candidate against this aligned baseline and the
same production controls. Use the existing exact trajectory/KPI/handle and
replay/seek gates. Shared event/digest/wire work should remain visible in the
measurements; a full C serializer is a separate architecture decision.

No journal bypass, direct serializer, kernel/binding tuning or production
integration was implemented in this milestone. The no-recording accelerator
opportunity remains separate from broader extension/semantic/packaging readiness.
The proposed next candidate requires a further instruction to proceed.

## Verification and retained evidence

273 prototype tests pass CPython (one sanitizer-only skip);272 pass PyPy
(sanitizer and CPython-profile skips, two known pytest-timeout configuration
warnings).156 existing production trace/ConWIP/invariance tests pass. Trace 301,
playback 26 and scene 16 TypeScript tests, all three typechecks and production
build pass. Both fixtures pass browser smoke in Chromium/Firefox/WebKit:6 cases.
Ruff/check/format pass. Old native fixtures, production runtime/Studio source,
native C/extension binaries, dependencies and CI are unchanged. Relevant native
sanitizer certification is inherited from the unchanged binary/source gate.

24 pre-measurement cases,24 matrix cases/120 timed samples/24 cold probes pass.
All preflight outputs match the final matrix's hashes, counters and trace
signatures. The current audit checks coverage/order/provenance/equivalence,
retirement/decision counts, backend-level digest invariance and ten corrupt-data
rejections. Historical verification checks 227 committed source/evidence files
against 1b234fc and rejects a changed source hash. Engine counters, journal sizes,
software/input/writer settings and trajectories remain invariant across studies.
Twelve CPython profiles cover 12 simulation and 8 writer threads, plus 8 separate
output-span passes. Large performance traces are checked and deleted; raw
samples, hashes, sizes/counts, diagnostic profiles and the small fixture remain.

Evidence: [matrix](../../prototypes/native_engine_dynamic/lifecycle-results/matrix.jsonl),
[summary](../../prototypes/native_engine_dynamic/lifecycle-results/summary.csv),
[metadata](../../prototypes/native_engine_dynamic/lifecycle-results/metadata.json),
[preflight](../../prototypes/native_engine_dynamic/lifecycle-results/preflight.json),
[comparison](../../prototypes/native_engine_dynamic/lifecycle-results/comparison.json),
[profile summary](../../prototypes/native_engine_dynamic/lifecycle-results/profile-summary.json),
[audit](../../prototypes/native_engine_dynamic/lifecycle-results/verification.json).
Frozen source SHA-256:
7f60b8c16f55a542328dce6771ea89935f8f3d9e5f3e663aaa09e924e13c9370.
The metadata records unchanged engine/extension hashes and complete software/
platform details (bigboss, CPython 3.14.3/PyPy 3.11.15(7.3.22), Simulatte 0.17.0,
SimPy 4.1.2, msgpack 1.2.3).

## Change and correctness

The projection now emits the actual ConWIP PolicyDecision before pool exit when
a WIP cap is configured. Uncapped immediate release emits no ConWIP decision.
After finish and WIP samples, it calls EntityRegistry.retire on the shadow actor
and deletes the corresponding live projection state. This removes completed
jobs from subsequent writer snapshots and releases shadow object references.
Native inputs, result arrays, completed-handle inspection, control history and
the journal remain intact. No C kernel, binding or production runtime changes.

Both capped native and production traces now contain 7N+7operations domain events:
one policy decision and retirement per job. Source records remain 4N+5operations.
Initial WIP samples still belong to the prelude in production and the simulation
phase in the bridge. Native/SimPy micro-event ordering and initial WIP maps can
still differ; equal lifecycle counts do not establish identical event digests.

Validation checks retirement/decision identity, timestamp, multiplicity and
ordering, complete operation/release/finish trajectories, KPI/WIP equivalence,
container integrity, reconstruction and digest. Historical job fields are checked
from creation/finish records rather than retaining completed entities in final
state. Targeted tests check exact pre/post-retirement cursors, completed handles,
unchanged result exports, weak-reference release, cap/no-cap and KPI/full levels.

Old native fixtures and measurement evidence are unchanged. The new lifecycle
fixture has 252 domain events,50 observers,15 chunks and digest
11bce447f79e37c176f9e4c2b4a8430fa458f0cf780fdf631bcf32e4ce29bb96.
Its source journal is byte-identical to the old fixture. Python and TypeScript
seek/state/KPI/scene parity covers both fixtures, including retirement cursors.

## Protocol and limits

Same 24 cases as the previous study:5k jobs, independent/live-feedback producers,
SPT, cap 12, production/native, CPython/PyPy, none/KPI/full. Five timed samples,
CPython 1/PyPy 7 warmups, separate cold/RSS probes; same shuffled order/seed 20261104.
All 24 pre-measurement gate cases pass before timing. Defaults and exclusions
match the earlier protocol: construction/generation/results/source and trace I/O,
complete writer shutdown/GC included; imports/DSO loading/parsing and post-run
validation excluded. Writer 10k-event/1 MiB/64 MiB limits, infinite latency, no fsync.
KPI TraceRecorder still constructs domain events and semantic digest.

Twelve separate CPython profile commands cover simulation/writer CPU and eight
unprofiled output-span passes. The standard Python profiler uses thread CPU with
same-thread immediate finalization; its instrumentation heavily perturbs call
costs. Use profiles qualitatively, call counts and snapshot/output-span evidence
for the mechanism, and unprofiled complete lifecycle samples for performance.
Wall output spans include scheduling/GIL/I/O waits and overlap with model work.
No PyPy profile, larger-workload scaling or direct native serializer is certified.

The historical comparison checks every recorded baseline source and evidence
file against its 1b234fc Git blob, then verifies unchanged engine/extensions,
input/software/writer settings, case order, trajectories/generation/feedback,
KPIs/WIP, engine counters and journal sizes. Before/after timings come from
separate runs; unchanged production controls quantify drift. This is a joint
decision+retirement candidate, not a policy-versus-retirement ablation.

## Reproduction

```sh
.venv/bin/python -m prototypes.native_engine_dynamic.trace_fixture
.venv/bin/python -m prototypes.native_engine_dynamic.production_benchmark matrix \
  --output prototypes/native_engine_dynamic/lifecycle-results \
  --pypy prototypes/native_engine/.build/pypy-env/bin/python
for producer in independent feedback; do
  for backend in production native; do
    for mode in none kpi full; do
      .venv/bin/python -m prototypes.native_engine_dynamic.production_benchmark profile \
        --input prototypes/native_engine/.inputs/jobs-5000-load-0.95.json \
        --producer "$producer" --backend "$backend" --mode "$mode" \
        --output "prototypes/native_engine_dynamic/lifecycle-results/profiles/$producer-$mode-$backend.json"
    done
  done
done
.venv/bin/python specs/research/native-engine-data/production_analysis.py \
  prototypes/native_engine_dynamic/lifecycle-results \
  --baseline prototypes/native_engine_dynamic/production-results --baseline-revision 1b234fc
```

Fixture generation is deliberate and must happen before freezing measured
sources; it writes lifecycle.*, preserving native.*. Reports and raw source/
binary/input hashes identify the required checkout. Prior studies require their
recorded checkout rather than this version's changed lifecycle validator.
