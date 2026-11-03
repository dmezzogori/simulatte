# Projection packing and copying profile

Research-only attribution of the unchanged provider hook and identity consumer at
signed `7580a00817bfcd5fc9337d9377d1b216392db3c3`. Product sources, prior experiments
and matching local0.17.0/provider-API2 wheels remain byte-identical. No optimization
or release is part of this step. `plant.py`, both cases, `projection.py` and
`reference_projection.py` are byte-identical frozen copies of the preceding gate.

The external installed runner creates54 serial fresh processes, each with baseline
and profiled managed simulations.24 live cells sample time65 before that poll;
18 history cells contrast128/8192/65536 completed jobs;12 callback cells add16/256
records at128 jobs. Both retentions and seeds100–102 are included; live cells also
span both controllers and FIFO/SPT. Complete identities are checked against the
frozen parser; endpoint artifacts/fingerprints and native/controller state must
remain identical. Historical endpoints check seeds100/101.

Seventeen operations isolate native atomic capture, stream/managed hooks,
composition-only replay, validation, the actual per-poll pipeline, callback
creation/freezing/packing, view freezing/detachment and the packed-size diagnostic.
Latency uses five randomized adaptive25ms blocks, normal GC, wall/thread CPU clocks
and a no-op control. cProfile self/cumulative/call-count data and Python traced
peaks are collected separately. Profile shares are instrumented attribution, not
unprofiled CPU shares. Replayed views do not substitute for fresh future-input
callbacks in production. Component latencies need not add exactly; closure error
is recorded. Traced peaks/net retained sites do not measure total allocation churn
or native malloc. This is a fixed-state query study, not whole-simulation timing.

```sh
.venv/bin/python -m pytest --no-cov -q prototypes/native_projection_profile/tests
.venv/bin/python -m prototypes.native_projection_profile.run \
  --core-wheel prototypes/native_provider_projection/.build/core/simulatte-0.17.0-py3-none-any.whl \
  --native-wheel prototypes/native_provider_projection/.build/native/simulatte_native-0.17.0-cp314-cp314-macosx_27_0_arm64.whl \
  --output prototypes/native_projection_profile/results/macos-final
.venv/bin/python -m prototypes.native_projection_profile.analysis \
  prototypes/native_projection_profile/results/macos-final --plot
```

`--quick --blocks 2 --target-ns 5000000` runs a four-cell preflight. The final runner
checks source/archive hashes, removes compilers from PATH, uses installed modules
outside the checkout and saves every raw batch/profile/tracing record. No tests,
compilers or process-memory sampling run during final query timings. The audit
checks all evidence hashes, exact baseline/endpoint neutrality, stressor isolation
and preservation of every base file. Figures are generated after measurement.
