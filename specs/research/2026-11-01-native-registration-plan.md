# Registration and advancement boundary experiment

Starting revision: `69010b7`, branch `feature/native-engine-feasibility`, 2026-11-01.
Davide authorized the next experiment after the [dynamic/ConWIP report](2026-11-01-native-hooks-dynamic.md).
This is one bounded performance iteration on that functional baseline, implemented inline.

Question: how much of the dynamic lifecycle cost can an amortized registration boundary recover,
and how much requires a producer that can supply independent jobs ahead of simulation time?

## Design fixed before measurement

- Keep scalar `add`, `advance`, event processing, dispatch, cap/EDD release, records and ownership unchanged.
- Add opt-in `NativeStream.add_many(exact_tuple_of_Jobs)`, flattening/copying input during timing and
  returning a stable handle for every job. One `nd_add_many` call registers the batch by running the
  existing C admission routine for each job. No advance, observers, policy callbacks or grants are skipped.
- Interleaved schedule: advance to the first arrival in each window, then register its jobs. Windows
  are fixed at 1, 32 and 256 jobs. Compare scalar and bulk at the same boundaries, as well as scalar
  window 1 versus scalar larger windows. Preserve the previous per-job driver exactly for window 1.
- Larger windows require future independent inputs to be known. Final trajectories/records must match,
  but Python can inspect those future registrations early; this does not preserve producer visibility
  or permit a feedback-dependent producer to assume the same lookahead.
- Eager diagnostic: no simulation advances while feeding, so feed time isolates validated registration,
  buffer conversion/copying, arrival scheduling and handles from execution. It is a different producer
  schedule, and its calendar population can affect later execution. Do not label interleaved feed as
  pure FFI overhead, or subtract eager feed as an additive decomposition of streaming time.
- Use current reference and queue-specialized compact Python controls under the same schedules on
  CPython/PyPy. No pre-registration, omitted handles or borrowed native views in any timed lifecycle.

## Evidence

Use the identical historical 50,000-job, load 0.95, SPT input, ConWIP cap 12, none/record modes.
64 cases: per interpreter/mode, 11 interleaved cases (three scalar backends x three windows plus native
bulk x two windows), and five eager cases (two scalar Python controls, scalar native, two bulk windows).
Five samples, one CPython/seven PyPy warmups, separate cold/RSS probes. Seeded shuffled case order,
serial workers, source/library/input hashes checked throughout. Include construction, copies, handle
creation, advancement/execution, detached results, close and explicit GC; exclude input/model parsing
and imports as before. Keep old results unchanged; write a new registration-results directory.

Before timing: RED/GREEN batch validation/ordering/ownership tests, existing suites on both interpreters,
ASan/UBSan for scalar and batch growth/lengths/allocation failure. Exact arrays, releases, record bytes,
KPIs and replay invariants must match independent controls across small FIFO/SPT/cap/mode fixtures.
Audit all coverage, identities, samples, timing sums, counters and hashes, and reject deliberately
corrupt evidence. Compare trajectory/record hashes to the prior study, without reusing its timings.

Report medians, ranges, cold/RSS, matched-schedule ratios, eager feed times and limitations. Keep the
bulk interface opt-in regardless of outcome. Stop after this iteration; no threshold-driven tuning.
No compiled Python extension, production integration, Studio encoding, push or merge is included.

## Completed outcome

All 64 cases, 320 timed samples and 64 separate cold/RSS probes pass the full evidence audit, including
prior exact trajectory/record parity and six corrupt-evidence checks. 213 tests pass on each interpreter;
scalar/bulk sanitizer exercises pass 143/144 allocation-failure positions. CPython bulk 32 improves
lifecycle by 1.94–2.06x, PyPy bulk 256 by 3.95–3.98x; selected native paths are 3.71–4.54x the strongest
compact PyPy control across all schedules. The [report](2026-11-01-native-registration.md) preserves
raw ranges, 15.28% PyPy recording variability and the producer-lookahead limitation. No further tuning.
