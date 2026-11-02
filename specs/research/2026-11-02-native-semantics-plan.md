# Simultaneous-event semantics experiment

Davide authorized the next experiment after the [failed gate](2026-11-01-native-gate.md).
Starting revision `31bf33e`; existing native feasibility worktree.

Align the native model's arrival/initialization, operation completion, policy
wake-up and resource dispatch ordering with the unchanged production actors.
The compact Python reference makes the phase model inspectable; actual production
remains the independent authority. Port validated phases to C without changing
the binding API, rich journal format or either event emitter.

- Turn the four-job counterexample into a production-equivalence requirement.
- Add deterministic quantized workloads with simultaneous arrivals/completions,
  tied EDD/SPT priorities, both producers and multiple caps. Check every native
  model against production and preserve cross-model record/result parity.
- Run the existing 288-case recorded gate, plus a broader seed sweep and strict
  boundary checks. Keep standard live-collector compatibility and buffered
  delivery as explicit remaining limitations. Identify the known production
  benchmark peak-export artifact separately from actual event-series peaks.
- Exercise C allocation/error paths under ASan/UBSan and the compiled boundary's
  existing sanitizer checks. Preserve old fixtures/evidence; use a new fixture
  if the current rich source/trace output changes.
- After semantics pass, freeze source/binaries/input and rerun contemporary
  production controls and native timings at 5k/20k, with the prior matrix's
  warm-ups, repeats, cold probes and full close/cleanup boundary. Compare old
  native behavior/performance only with verified committed historical evidence.
- Record the scope, evidence, performance impact and remaining limitations in
  the repository and Cortex, then commit locally. No push or public integration
  is included in this request.

The first hypothesis is an urgent job-initialization event after release and a
normal, coalesced policy wake-up scheduled before resource dispatch at every
operation departure. These must be validated against production, not assumed
sufficient from the four-job example alone.
