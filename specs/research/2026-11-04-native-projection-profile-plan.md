# Projection packing/copying profiling experiment

Davide authorized profiling packing/copying in the provider hook and identity
composition after signed `7580a00817bfcd5fc9337d9377d1b216392db3c3`.
Work inline. This step measures and attributes cost; no product optimization,
fingerprint/schema/API migration, push, publication or deployment-baseline work.
Preserve every base file and reuse the exact validated local 0.17.0 wheel pair.

- Run serial fresh processes in an external clean installed environment, without
  runtime compilers or prototype imports. Freeze source/wheel hashes and copy
  byte-identical model, consumer and parser references. Check archive bytes and
  actual loaded image origins. Keep setup/startup outside query timings.
- Profile a synchronized live boundary at time65 before that poll, using both
  original controller fixtures, FIFO/SPT, full/bounded retention and seeds100–102
  (24 cells). Compare baseline and profiled runs' final fingerprints/artifacts and
  controller/native state before/after each measurement. Require exact public-hook
  and frozen-parser identity equality. Include the actual validation and packed-size
  diagnostic work from the preceding per-poll experiment.
- Separate unprofiled latency measurements for raw atomic native snapshot, stream
  projection, managed projection, identity composition replay on a captured view,
  validation, whole hook+identity and full query pipeline. Isolate callback creation,
  freeze, prepared packing, detachment and size packing on equivalent captured
  inputs. Replayed inputs never replace the authoritative production callback.
- Use adaptive batches targeting25ms, five randomized measurement blocks per cell,
  wall and thread CPU timers, normal GC, warmup and a loop-overhead control.
  Do not run cProfile or allocation tracing during latency batches. Export raw
  batch counts/times, not only averages. Do not assume replayed component times add
  exactly to the real pipeline; report their closure difference.
- Collect separate cProfile self/cumulative/call-count evidence for managed hook,
  composition-only and the actual pipeline, plus independent tracemalloc peaks.
  Avoid double-counting inclusive times; report profiler distortion against the
  unprofiled measurements. Tracemalloc peaks/net snapshots are not total allocation
  churn and exclude native malloc. Attribute freeze/canonicalization, MessagePack,
  detachment, callbacks and native observation using explicit function groups.
- Contrast completed history128/8192/65536 jobs with a constant-shape callback
  (counter encoding widths may change), both
  retentions and three seeds (18 synthetic cells), using lazy input and synchronized
  managed execution. Separately add16/256 records to the pure callback at128 jobs,
  both retentions and three seeds (12 cells). All54 cells share the same measurement
  protocol. These stressors distinguish native-history scaling from callback/tree
  conversion scaling; they do not claim arbitrary-model representativeness.
- Test measurement aggregation, frozen references, real operation/state neutrality
  and detection of bad/mutating inputs using nearby research-test conventions.
  Run focused existing native checks; no root-suite/sanitizer repeat is needed
  with product/provider bytes unchanged. Do not run tests/compilers during timing.
- Audit raw evidence, preserve every base byte, save a report and a standard static
  profiling figure, signed local commit and Cortex checkpoint. Identify measured
  bottlenecks, uncertainty and a concrete next experiment without claiming an
  unimplemented optimization or unsafe future-state caching.
