# Bounded-memory streaming research

This is an isolated Simulatte v3 experiment based on `9ecc262`, following the
optional native packaging gate. It does not modify the installed native package,
core API, prior C engine transitions or earlier experiment evidence.

```sh
.venv/bin/python -m prototypes.native_engine_streaming.build
.venv/bin/python -m pytest prototypes/native_engine_streaming/tests -o addopts='' -q
.venv/bin/python -m prototypes.native_engine_streaming.build --sanitize
prototypes/native_engine_streaming/.build/sanitizer
.venv/bin/python -m prototypes.native_engine_streaming.experiment --output prototypes/native_engine_streaming/.build/recheck
.venv/bin/python -m prototypes.native_engine_streaming.analysis prototypes/native_engine_streaming/.build/recheck --plot
```

Compile and test before the serial timing matrix, never concurrently with it.
Choose a new output directory for each matrix; committed evidence is never overwritten.
The auditor permits a descendant commit while verifying every base tracked file.
Results contain frozen source/binary/generator hashes, cold-process rows and an
independent audit. The existing packaged API retains its full-history contract.

```python
from simulatte.experimental.native import Job
from prototypes.native_engine_streaming.stream import Stream, ExpiredHandleError

with Stream(servers=2, retention="bounded") as engine:
    handle = engine.add(Job(0, (0, 1), (2, 3), due=10))
    engine.advance(6)
    print(engine.snapshot()["scalars"])
    try:
        engine.inspect(handle)
    except ExpiredHandleError:
        print("completed job deliberately forgotten")
```

`retention="full"` is the research default and keeps completed inspections.
Bounded mode compacts only after a successful `advance`/`run`, never during an
in-flight event phase. Live handles are monotonic 64-bit logical IDs; expired IDs
never alias another job when physical slots are reused. Reaching ID/sequence or
floating-clock limits fails explicitly. Full trajectory exports are still supplied
by the unchanged packaged `NativeStream`; this research API exposes inspections,
scalar snapshots and state commitments.

Completed inputs/trajectories are incrementally SHA-256 committed in actual
completion order. Only one completion batch is materialized, then released.
`NBST0001` serializes live logical execution; `NBDS0001` combines its bytes with
that historical commitment, current KPIs and explicit compact controller state.
These research schemas intentionally differ from installed `NSTA0001` and
`native-production-v1`. Retention mode and physical layout never enter the state.

The input callback must describe all future Python input/controller decisions.
The measurement callback includes the counter generator seed/cursor/spacing,
finite job limit and window size. It does not retain the generated workload.
The `StreamingProduction` research adapter exercises the existing managed
`Environment` backend lifecycle with semantic digest enabled and dynamic input only. It does not establish
Coordinator/run-store/Studio memory bounds or a portable installed backend.

Native storage is proportional to the high-water outstanding jobs/operations,
calendar and one input/completion window, with fixed server and scalar state.
It is not bounded independently of the workload. ConWIP limits the floor, not
an overloaded pre-shop pool. Overload is measured as a negative control. Large
user-retained handle/history collections or eager future input remain the user's
memory, outside the bounded-controller contract.

Full and bounded runs must have identical generated inputs, KPI windows/scalars,
completion commitments and canonical state. Gate tests compare the actual
unchanged packaged engine and ordinary Python collectors; sanitizer tests sweep
mutating and read-only allocation failures and stale-ID reuse. All old tracked
files are checked against the base commit by the audit.
