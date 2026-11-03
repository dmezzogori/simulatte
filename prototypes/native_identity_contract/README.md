# Native identity projection experiment

Research-only wrappers around byte-identical copies of the fixed-specification and
KPI-fed admission fixtures. No core, provider, default digest or public API change.

`projection.py` defines three versioned commitments. Trajectory/control describes
realized native history/live state and admission targets/jobs. Reporting state
describes the declared Horizon/window, KPI catalog and current stock snapshot.
Reproduction state binds both to the original backend authority, including the
complete Python callback state. Only the last layer is eligible for the experiment's
reproduction comparison; existing Coordinator reproduction stays authoritative.
These are hashes, not restorable checkpoints or permission to reuse results.

The trajectory digest is encoding-specific: full and bounded native codecs must
not be compared to each other as a common physical encoding. Reporting identity
does not represent arbitrary collector history, trace content or exported reports.
Caller-owned action/source state is a model responsibility. Production code would
need a public projection hook; this experiment uses existing private owner/lock
access and rejects unknown native encodings.

Capture checks projection against actual synchronized raw state. The exported
envelope validates component hashes and authority links; it is not an independent
proof against a malicious producer. In bounded mode it exports the live-state
hash rather than the full raw preimage. Trust still rests on the frozen capture
implementation, installed runtime, and the ordinary Coordinator reproduction.

Run the focused checks with `.venv/bin/python -m pytest --no-cov -q
prototypes/native_identity_contract/tests`. Run the installed experiment with:

```sh
.venv/bin/python -m prototypes.native_identity_contract.run \
  --core-wheel prototypes/native_retention_integration/.build/core/simulatte-0.17.0-py3-none-any.whl \
  --native-wheel prototypes/native_retention_integration/.build/native/simulatte_native-0.17.0-cp314-cp314-macosx_27_0_arm64.whl \
  --output prototypes/native_identity_contract/results/macos-final
.venv/bin/python -m prototypes.native_identity_contract.analysis \
  prototypes/native_identity_contract/results/macos-final
```

`run.py` freezes research/core/provider source hashes, verifies wheel contents,
installs into an external clean environment, removes compilers from runtime PATH,
runs 72 primary cases, 12 source-deleted reproductions and 10 query/baseline controls,
then measures 18 serial bounded cases plus 5 controls (including 2 more attempts).
Proof streams are content-addressed; evidence and an audit stay in the repository.
No compiler or pytest runs during resource measurement. Timings include Coordinator
preparation, child startup, queries, serialization and artifact checks.
