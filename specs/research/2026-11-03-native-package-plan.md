# Optional experimental native provider packaging

Davide authorized packaging after the macOS/Linux declared-asset gates. Implement
inline in the existing isolated research worktree. No publication, push or merge.

- Keep the base distribution pure Python. Add a separately built
  `simulatte-native` companion, selected by the `native` extra on supported
  GIL-enabled CPython/macOS/Linux. Preserve the validated two-image engine/binding
  build and strict stock-KPI arithmetic. Vendor the five unchanged C/header files,
  retaining source hashes and checking them against the research implementation.
- Expose `Job`, `JobHandle`, context-managed `NativeStream` and reusable managed
  `NativeProduction` from `simulatte.experimental.native`. Fixed submitted jobs
  are covered by native state; dynamic admission requires an explicit pure input
  state callback so future Python controller/input state can be fingerprinted.
  Retain Horizon/empty-layout/scalar-KPI restrictions and owner-safe handles.
- Build platform/CPython-ABI tagged wheels plus a complete standalone sdist.
  Installed assets use stable distribution paths, a build manifest tied to wheel
  RECORD, ABI checks, pre/post byte identity and actual dependency-origin checks.
  Installed package verification remains distinct from project-source bundle
  declarations; no model-side binary copying or compiler at runtime is required.
- Add clean base-only and native wheel installs outside the checkout, managed
  model execution/reproduction, source deletion, corruption and ABI rejection,
  sdist roundtrip and incompatible-wheel tag checks. Exercise macOS arm64 and
  Linux x86-64 with CPython 3.11 and 3.14 locally/remotely where available. Add CI
  jobs for macOS/Linux CPython floor/current/ceiling ABIs.
- Run appropriate unit/managed/native regressions, type/lint/docs/build checks.
  Record raw clean-install evidence and hashes, report, signed local commit and
  Cortex checkpoint. No broader actor/policy/full-trace features, Windows/PyPy/
  free-threading or remote-native-worker support. No new performance claim.
- Version pins must match the locally built core/provider pair. Publication and
  coordinated release versioning remain separate maintainer work; no package is
  uploaded by this task or by the validation-only native CI job.
