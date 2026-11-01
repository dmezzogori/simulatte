# Direct production baseline and profiling

Davide authorized this experiment after the Studio trace result. The previous
full-trace comparisons all used the same journal/projection bridge. This study
adds ordinary production Simulatte directly connected to TraceRecorder.

1. Use unmodified Environment, ProductionJob, ShopFloor, Server, PreShopPool and
   ConWIP, SPT/cap 12 and the historical load .95 input. Export operation start/end
   arrays through two public synchronous hooks; no replacement scheduling or
   policy implementation. Share the scalar independent/live-feedback producer.
2. Before timing, establish exact job-generation, feedback, release, operation
   and finish trajectories on both interpreters. Check all aggregate KPI values
   within the existing numerical tolerance. Verify traces independently; their
   semantic digest/event streams need not match because production emits entity
   retirement and policy events and uses SimPy micro-event order.
3. Measure 5,000 jobs, two producers, both CPython/PyPy, production versus compiled
   native, none/KPI/full recording: 24 cases, five samples, 1/7 warmups, separate
   cold/RSS probes, serial shuffled order. Include backend model construction,
   dynamic generation, trajectory export, all source/trace I/O and complete
   close/GC. Imports, input parsing and post-run validation are outside timing.
   KPI mode uses the real recorder at level=kpi, including its semantic digest,
   ten common scalar KPIs and the same two WIP series. Native KPI/full retain
   the journal/projection bridge; no direct encoder or production optimization.
4. In separate CPython runs, profile simulation and writer threads using each
   thread's CPU clock, retain function-level self/cumulative times, and group
   exclusive self time. Compare unprofiled wall/process CPU measurements with
   profiles; do not treat instrumented time or inclusive calls as additive wall
   cost. Distinguish bridge projection, shared digest/event encoding, writer
   replay/snapshots/compression and I/O. No PyPy JIT claim from CPython profiles.
5. Retain raw samples, provenance, trace counts/sizes, hashes, profiling evidence,
   corruption-rejecting audit and a report. Check production source is unchanged.
   Stop at evidence and recommendations; do not implement the next optimization.

Completed: 24 correctness preflight cases, 24 measurement cases/120 timed samples/
24 cold probes and 12 CPython diagnostic profiles pass the provenance/equivalence
audit and ten corruption checks. Native CPython is 23.36x/6.12x faster than production PyPy
without recording, but full tracing takes 1.53x/1.50x as long. Profiles identify
duplicate shadow replay and 4.63x/74.08x aggregate snapshot growth. The
[report](2026-11-01-native-production.md) recommends lifecycle alignment as the
next bounded experiment; it was not implemented in this milestone.
