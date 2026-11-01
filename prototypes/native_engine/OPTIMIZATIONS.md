# Measured optimization ledger

Budget: at most three iterations after a correct baseline. Three iterations used: Python queue specialization,
the subsequently authorized binding-only follow-up and the owned-buffer hook experiment. No static native
C algorithm optimization. The separate dynamic/ConWIP milestone is a new functional slice and baseline.

## Baseline

Commit `c08b066` contains correct independent C and Python engines plus the actual current-Simulatte adapter.
`results/baseline-pilot.jsonl`: high-contention SPT, 5k/50k, none/record, all three engines; one warm-up,
three measured samples, separate fresh memory probe. All trajectory and record fingerprints match.
An earlier pilot overlapped briefly with PyPy test setup; it was discarded in full and rerun serially.

At 50k: current/reference/native totals were 3.063/0.429/0.130 s without recording, and
3.270/0.558/0.140 s with recording. These are equivalent-work totals, not the earlier LumsCor measurements.
`pilot-profile.txt` is a diagnostic cProfile run, not benchmark timing: reference grant logic consumes
0.389 of 1.278 profiled seconds, including repeated SPT min scans; native Python construction dominates
its profile (0.352 of 0.387 seconds). Profiling overhead prevents direct comparison to wall timings.

## Iteration 1: optimized Python comparison

Hypothesis: deque FIFO and a heap keyed by (duration, enqueue sequence) for SPT reduce Python dispatch
overhead without changing useful event ordering. The separate `TunedRunner` preserves the simple reference
as an oracle. Hook mode retains enqueue-order lists and the same Python callback boundary.
Tests compare 400 tied workloads, exact trajectories and recording bytes, plus the full benchmark hashes.
Measure reference and tuned together in `results/final-matrix.jsonl`, including CPython and PyPy.
No C algorithm, binding or observation optimization is included in this iteration.

Measured outcome: tuned built-ins give 1.04–1.23x reference throughput on CPython and 0.91–1.04x on PyPy.
The representative CPython hook path regresses 6.3%; the extra delegation is a plausible contributor,
not isolated by these measurements. Preserve both engines. Stop after this one iteration: native built-ins
already exceed the evaluation thresholds versus current Simulatte, while PyPy/hook comparisons show the
architectural tradeoff clearly. A 16-case fresh-process repeat preserved the conclusions (median changes
within 2.32%). No performance change was made in response to the final matrix.

Final measured effects and stopping rationale are in the research report. No second or third iteration
is required by the goal: the budget is a ceiling, not a performance target to pursue indefinitely.

## Correctness repair (not an optimization iteration)

Independent review found the baseline feeder could round absolute arrivals when reconstructing them
through relative timeouts (`.2 + (.9 - .2) != .9`), and could split tied batches. Added a red-then-green
regression and schedule absolute normal-priority SimPy events in the feeder. Processing/resources remain
unchanged. The partially completed full matrix was discarded and restarted after the fix. The earlier pilot
passed exact fingerprints for its generated inputs; keep it as historical baseline evidence only.

The frozen benchmark launcher initially resolved the PyPy virtualenv executable symlink, losing its
environment and failing import before the first PyPy timing. The matrix resumed with a shell launcher
that execs the virtualenv path, preserving all measured engine sources. After the full matrix, the driver
was corrected to retain a lexical absolute executable path, and representative CPython/PyPy cases were
repeated in fresh processes. This launcher repair changes no measured engine code or timing boundary.

## Iteration 2: binding-only follow-up (2026-11-01)

Davide authorized the [bounded plan](../../specs/research/2026-11-01-native-engine-bindings-plan.md) after
review found redundant input checks, ctypes argument splatting and per-element extraction dominated native
lifecycle cost. Hypothesis: standard-library array/from_buffer input and memmove extraction can remove
most of this overhead without modifying C, callbacks, other runners or timing boundaries. Exact validated
Workload/Job values bypass duplicate per-element checks; duck-typed/subclass inputs retain the checked path.
Results still materialize detached lists inside extraction timing, rather than returning cheaper views.

Fresh before/after evidence: `results/binding-{before,after}.jsonl`, 24 cases each (original 16-case repeat
plus eight tuned controls), five samples/case and separate cold/RSS probes. Same release library, historical
input hashes and interpreters; all trajectories/recordings/counters agree. Both prototype suites pass 107
tests before review and 111 after its boundary correction; ASan/UBSan and lint/format pass. Analysis and
metadata are linked from the follow-up report. The first candidate's samples/source are retained in
`results/binding-pre-review/`; final candidate timings were rerun after the correction. This is a
correctness repair, not an additional optimization iteration.

CPython native built-in none: 127.25 → 41.49 ms (3.07x); record: 137.52 → 48.33 ms (2.85x). Against the faster
fresh compact PyPy engine: 2.22x → 6.89x without recording and 2.44x → 7.02x with recording. Hooked native
improves 1.22–1.23x but stays at 0.92x / 1.02x the best PyPy baseline. Unchanged controls drift −0.18% to +2.41%,
well below the built-in effect. Keep the actual list materialization cost visible; 35–40 ms / 8x was a
review hypothesis, not a threshold. Stop after this iteration; no callback or C changes to chase a target.

These conclusions cover only 50k/.95/SPT. The historical full-matrix report/evidence above stays unchanged.
See the [binding follow-up report](../../specs/research/2026-11-01-native-engine-bindings.md) for phase data,
limits, reproduction and the revised conditional-go assessment.

## Iteration 3: owned-buffer hook experiment (2026-11-01)

Davide authorized testing the Python extension boundary before broader native work. Compare default
tuple callbacks with an opt-in owned, read-only numeric-buffer snapshot, preserving candidate order and
every grant call, including singletons. Borrowing unsafe pointers or silently skipping/batching decisions
would change the contract. The default remains tuple; static C/ABI and its release binary are unchanged.

Fresh equivalent-work hook cases include native built-in/tuple/buffers, reference built-in/tuple/buffers,
tuned built-in/tuple, CPython/PyPy and none/record at 50k/.95/SPT. Owned-buffer native throughput improves
1.06–1.07x on CPython but falls to 0.77–0.78x on PyPy. This representation alone does not resolve Python
policy cost. Stop after this one alternative; no additional tuning to chase a target.

The [hook/dynamic report](../../specs/research/2026-11-01-native-hooks-dynamic.md) retains all 80 fresh cases
(32 hook +48 dynamic), independent validation, source/binary/input enforcement, cold/RSS and limitations.
The separate dynamic C library demonstrates incremental registration, stable handles and ConWIP cap/EDD
release. Its CPython-native lifecycle is 1.79–2.24x the best compact PyPy baseline on that slice; do not
apply the earlier static roughly7x comparison to dynamically registered workloads.
