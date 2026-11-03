# Fixed job specifications and reporting-independent admission

Research from signed 8501694; all previous files and production/provider/C/API
sources remain unchanged. The standalone installed `plant.py` defines immutable
routes, durations, nominal offered times and absolute due dates by seed/index.
Admission chooses actual plant entry time without moving deadlines or changing
service demand. A lazy source offers 12 jobs every 16 units, spaced by 1/8 unit;
there is no materialized template or pending-source queue.

The controller maintains a bounded integer EWMA of current pool fraction:
`q = (q + floor(256 * pool / live)) // 2` (zero for an empty system). Hysteresis
tightens at64 and relaxes at16; hard12/tight6/floor4, or hard8/tight4/floor2.
The control law does not read reporting KPIs. A hard cap bounds owned live handles.
The callback commits seed/cursor, live metadata, pressure/mode, phase, next wake,
configuration and rolling specification/admission/control commitments.

Control and reporting gzip streams and final artifacts are separate. Control
proofs are compared byte for byte across reporting warmups 0/10/40; reporting
values may change. Ordinary Python/full native/bounded native must agree for each
window. Python semantic digest equality across windows is checked separately;
native opaque codecs may commit reporting state as well as physical state.

```sh
python -m pytest prototypes/native_fixed_admission/tests -o addopts= -q
python -m prototypes.native_fixed_admission.run \
  --core-wheel /path/to/core.whl --native-wheel /path/to/native.whl \
  --output /path/to/new-output
python -m prototypes.native_fixed_admission.analysis /path/to/new-output --plot
```

`--quick --sizes 256 1024 --repeats 1` runs an installed preflight. The complete
gate has 60 correctness cases, 14 source-deleted fresh reproductions, 23 memory/
query/window/repetition cases and two extra repeated attempts. It uses clean copied
wheel assets outside checkout, Git-only runtime PATH and no prototype imports.
The ordinary Coordinator, semantic digest, KPI trace and publication path remain
active. Memory cases retain only rolling commitments; correctness proofs stream
to disk. Raw pressure statistics do not reset at reporting warmup.

Run tests and compilers before the serial memory matrix, and keep frozen sources
unchanged during it. The unchanged monitor samples simultaneous parent/descendant
RSS every 40 ms. The child records its own peak at native backend close, after
final state hashing and recorder closure; individual peaks are separate from
sampled tree peaks. Capacities and timing/PIDs remain external diagnostics.

The controller also records source-to-admission delay in exact eighth-units.
Engine flow-time KPIs begin at plant entry; fixed absolute deadlines and explicit
source delay prevent moving the origin from hiding admission postponement.
This is a research fixture, not a production policy-quality or release claim.
