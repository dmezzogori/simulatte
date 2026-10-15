# Native prototype implementation plan

> Agentic execution: use subagent-driven-development for the bounded C task and review; parent integrates
> the tightly coupled Python and benchmark tasks. Davide explicitly authorized autonomous execution.

Goal: implement and evaluate the CONTRACT.md slice, ending in evidence rather than a promised speedup.
Architecture: common validated inputs/results; actual Simulatte adapter; independent Python event model;
standalone C library with ctypes binding; identical binary recording and independent replay validation.
Tech: Python 3.11+, C17, local clang, ctypes, pytest; PyPy comparison where available.
Spec: [CONTRACT.md](CONTRACT.md). All implementation files stay below this directory; reports may use specs/research.

## Global constraints and review focus

- No production changes, no deployment/push, local commits only.
- Exact tied-event semantics, candidate membership and hook exceptions, finite scheduling,
  ownership after failure/close, and benchmark equivalent work are load-bearing review points.
- No optimization before correct baseline; maximum three measured iterations thereafter.
- Recording is the defined prototype format, not Studio tracing.

## Tasks

- [x] 1. Write failing hand-derived/reference tests; implement model.py, recording.py and reference.py.
  Reference test: three jobs of duration3/2/1 arriving0/1/1 on one server. FIFO starts0/3/5,
  SPT starts0/4/3; both complete6. Validate invalid inputs and exact cross-route ties.
  Run `.venv/bin/python -m pytest -o addopts='' prototypes/native_engine/tests -q`; commit green core.
- [x] 2. Implement current-Simulatte baseline.py with observation-only wrappers and real core classes;
  test exact trajectories/records against reference on tied and random workloads; add grant-time hook path.
- [x] 3. Delegate C core, native.py binding, build.py and native sanitizer harness to a bounded implementer.
  Consume model.Workload and common Runner API from contract. Native tests first; require standalone
  ASan/UBSan plus Python parity/errors. Reviewer checks semantics, allocation and error paths; integrate fixes.
- [x] 4. Add independent validation.py, deterministic workload generation and benchmark driver. Test decoder
  corruption/lifecycle failures, fingerprint mismatch detection and real small benchmark subprocess execution.
  Obtain correct equivalent baselines before measuring optimizations. Preserve raw JSON and input/source hashes.
- [x] 5. Investigate baseline profiles; perform zero to three documented measured optimization iterations.
  Add optimized Python/PyPy comparison, full 5k/50k matrix, separate memory measurements and reproducibility checks.
- [x] 6. Whole-branch review and appropriate regression checks; document implementation, measurements,
  risks/gaps, and evidence-backed recommendation. Verify production unchanged; commit; checkpoint Cortex.

## Rulings and progress

- 2026-10-15: User's explicit autonomy supersedes intermediate spec/plan approval gates in skills.
- Use positive durations and unique servers per route to match current ProductionJob's per-server state.
- Preserve current useful event ordering with five native phases; noop SimPy events may be omitted.
- ctypes chosen for a small independent C boundary and availability on both installed interpreters;
  this is a prototype binding choice, not a major-version packaging decision.
