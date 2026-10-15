# Measured optimization ledger

Budget: at most three iterations after a correct baseline. One iteration used; no native optimization yet.

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

Final measured effects and stopping rationale are in the research report. No second or third iteration
is required by the goal: the budget is a ceiling, not a performance target to pursue indefinitely.

## Correctness repair (not an optimization iteration)

Independent review found the baseline feeder could round absolute arrivals when reconstructing them
through relative timeouts (`.2 + (.9 - .2) != .9`), and could split tied batches. Added a red-then-green
regression and schedule absolute normal-priority SimPy events in the feeder. Processing/resources remain
unchanged. The partially completed full matrix was discarded and restarted after the fix. The earlier pilot
passed exact fingerprints for its generated inputs; keep it as historical baseline evidence only.
