# Provider-owned state projection gate

`NativeStream.state_projection()` and `NativeProduction.state_projection()` expose
an additive, versioned experimental query. The provider binding captures native
state and stock KPIs atomically under its busy guard; provider Python code derives
trajectory material and the original native commitment. The managed query binds
the full controller callback, scope and reporting configuration under the owner
lock. Default state/reproduction rules and native event transitions stay intact.

The generic hook does not know admission actions. `projection.py` composes the
previous model-specific identity envelope using only the public managed query.
Its default `hook` mode never invokes the binary parser. `reference_projection.py`
and both fixture copies are byte-identical frozen references from the preceding
experiment. `compare` mode requires exact equality of every captured envelope;
`reference` mode measures the old path. Controls use the same model source/inputs.

Run focused tests with:

```sh
.venv/bin/python -m pytest --no-cov -q tests/experimental/test_native_projection.py \
  prototypes/native_provider_projection/tests
```

Build the local matching pair with `uv build --wheel --out-dir
prototypes/native_provider_projection/.build/core` and `uv build native --wheel
--out-dir prototypes/native_provider_projection/.build/native`, then run:

```sh
.venv/bin/python -m prototypes.native_provider_projection.run \
  --core-wheel prototypes/native_provider_projection/.build/core/simulatte-0.17.0-py3-none-any.whl \
  --native-wheel prototypes/native_provider_projection/.build/native/simulatte_native-0.17.0-cp314-cp314-macosx_27_0_arm64.whl \
  --output prototypes/native_provider_projection/results/macos-final
.venv/bin/python -m prototypes.native_provider_projection.analysis \
  prototypes/native_provider_projection/results/macos-final
```

The runner freezes sources, checks wheel bytes, installs outside the repository
and removes runtime compilers. The gate runs72 primary cases,12 source-deleted
reproductions and18 capture/baseline/hook/reference controls. Serial resource
cases use18 bounded scaling cases across3sizes×3seeds×2controllers,30 query-cost
controls (disabled, initial/final only, hook every poll, reference every64/everypoll)
and one same-parent case with two further reproductions:153 fresh attempts total.
Timing includes Coordinator preparation, startup, queries and artifact checks;
no tests/compilers run during final measurement. Cost groups use three paired
seeds; they are exploratory timings, not production benchmarking guarantees.

The audit checks the preceding invariant/divergent-control semantics, physical
Python parity, exact hook/parser envelope agreement, historical artifacts/default
fingerprints, source-deleted reproduction, detached bounded sizes and predeclared
RSS limits. Nine implementation/docs files are expected to change from the signed
base; all prior research fixtures/evidence and event-transition sources are checked
byte-for-byte. Complete identity remains authoritative; trajectory does not permit
reuse, restoration or comparison across different encodings.
