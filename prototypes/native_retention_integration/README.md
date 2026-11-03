# Installed bounded-retention Coordinator gate

V3 research after successful v2/Studio; no deployment or portable-release decision.
Full retention remains default. The provider has ordinary linked C units and no
prototype/runtime compiler dependency. Bounded retention uses its distinct state
schema; compare physical KPIs across modes and reproduce state within each mode.

Build the matching local core/native pair (Studio assets must already be built):

```sh
uv build --out-dir prototypes/native_retention_integration/.build/core
uv build native --python 3.14 --out-dir prototypes/native_retention_integration/.build/native
```

Run `python -m prototypes.native_retention_integration.run --help`, supply the
matching wheels and a fresh output directory. Use the repository venv Python.
`--quick --sizes 1024 --repeats 1` is a preflight; the default is the full matrix.
The runner creates an external clean venv/project workspace, installs only those
wheels, and executes from outside the checkout with Git-only PATH and invalid CC.
The workspace path is recorded and retained for inspection. No compilation occurs
during a simulation. The `analysis` module takes that output path and `--plot`.
Run audits and tests after timing, not concurrently with the serial final matrix.

The gate covers two slots, fixed/dynamic input, FIFO/SPT, full/bounded modes,
source-deleted fresh reproductions and private-copy binary corruption rejection.
The memory matrix uses lazy 64-job windows, digest/KPI traces, three paired seeds
per size, overload negative controls, extra pure queries and repeated attempts in
one Coordinator parent process. Source, wheel and input hashes are independently
checked. Prior research/evidence bytes and C event-transition bodies are checked.

A host monitor samples Coordinator and descendants with `ps` every 40 ms. The
simulation child also records its own peak RSS at close in an external diagnostic
file. RSS/times/PIDs never enter published artifacts or semantic fingerprints.
The sampled simultaneous tree peak and individual process peaks are different
metrics; short allocation bursts can escape tree sampling. The external monitor
is outside the measured Coordinator tree. Disk trace/store growth is reported
separately from resident memory. This is a bounded-workload test, not an infinite
memory guarantee or support claim for arbitrary policies/actors/full traces.
