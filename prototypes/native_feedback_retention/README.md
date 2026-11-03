# Feedback admission with bounded native retention

Research from signed d26edb9; no production/core/provider source changes. A copied
standalone `plant.py` uses ordinary public Python actors or the installed native
adapter. It owns at most the fixed hard-limit of live handles, generates a job
only after credit is available, and retains no input/decision/job history list.

Pool state selects normal versus shortened reversed routes. The current window's
tardy fraction selects the hard or half-size admission target. At most three
jobs are admitted per urgent right-open boundary; full target provides independent
backpressure beyond ConWIP's floor cap. The callback commits source seed/cursor,
live identities, phase, next wake and all future controller parameters.

Correctness proofs stream deterministic gzip rows to disk. Native/Python rows
include normalized completion detection (expired handles identify completion,
not historical completion timestamps), live observations, all eight KPI values,
credit/choice and actual constructed jobs. An independent audit reconstructs the
law and counter inputs. Final controller commitments enter a typed domain event
and native input callback. Full/bounded schemas remain distinct.

Run research tests before timing:

```sh
python -m pytest prototypes/native_feedback_retention/tests -o addopts= -q
python -m prototypes.native_feedback_retention.run \
  --core-wheel /path/to/core.whl --native-wheel /path/to/native.whl \
  --output /path/to/new-output
python -m prototypes.native_feedback_retention.analysis /path/to/new-output --plot
```

`--quick --sizes 256 1024 --repeats 1` is an installed preflight. The final matrix
uses three paired seeds, 4k/32k/262k decision ticks, full/bounded modes, pure-query
controls and same-parent fresh reproductions. Memory cases keep rolling hashes
only; correctness cases export streamed rows. The ordinary Coordinator, digest,
KPI trace and artifact publication path stays active. No tests/compilers or source
edits during timing. RSS/times/PIDs/capacities are external diagnostics.

The monitor reuses the unchanged previous gate helper: 40 ms simultaneous process
tree RSS sampling plus child self peak RSS. Peaks are separate metrics.
Child self peaks are captured at backend close, after final state hashing and
recorder closure; native capacity counts are captured at the final live boundary.
Bounded live work/controller state and finite advance batches remain required; this is
not a production release, arbitrary actor support or an infinite-memory claim.
